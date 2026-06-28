from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.jobs.creator_verify import _has_audio_episode, verify_creator_task
from catalog.jobs.discover import DiscoverGenerator
from catalog.models import (
    Edition,
    IdType,
    ItemCredit,
    Podcast,
    PodcastEpisode,
    VerifiedCreator,
    creator_identity_candidates,
    match_creator_identity,
    resolve_creator_identity,
    user_controls_owner,
    user_owned_claims_q,
)
from catalog.sites.rss import RSS
from common.models import SiteConfig
from journal.models import Article, Mark, ShelfType
from mastodon.models.bluesky import BlueskyAccount
from mastodon.models.mastodon import MastodonAccount
from users.models import User

_BACKEND = "mastodon.auth.OAuth2Backend"

# a parsed-feed episode list with one audio enclosure, so a feed passes the
# "has an audio episode" guard during verification
_AUDIO_EPISODES = [
    {"enclosures": [{"url": "https://ex.example/1.mp3", "mime_type": "audio/mpeg"}]}
]


def _podcast(title="Test Pod", feed="podcast.example.com/feed.rss") -> Podcast:
    return Podcast.objects.create(
        localized_title=[{"lang": "en", "text": title}],
        primary_lookup_id_type=IdType.RSS,
        primary_lookup_id_value=feed,
    )


def _client(user) -> Client:
    c = Client()
    c.force_login(user, backend=_BACKEND)
    return c


def _verified(item, identity, matched="@x@example.org") -> VerifiedCreator:
    return VerifiedCreator.objects.create(
        item=item,
        owner=identity,
        state=VerifiedCreator.State.VERIFIED,
        matched=matched,
    )


def _capture_discord(monkeypatch: pytest.MonkeyPatch, target: str) -> list:
    """Replace the `discord_send` bound in `target`'s module with a stub that
    records its calls, returning the list of (args, kwargs) tuples."""
    calls: list = []
    monkeypatch.setattr(target, lambda *a, **k: calls.append((a, k)) or True)
    return calls


def _restrict(identity, level=2) -> None:
    """Set the Takahe identity's moderation restriction (1=limited, 2=blocked)."""
    from takahe.models import Identity

    Identity.objects.filter(pk=identity.pk).update(restriction=level)


def _make_remote_identity(username="alice", domain_name="mast.example"):
    """Create a remote APIdentity (and its Takahe Identity) for tests that
    attribute a verified work to a linked Mastodon account."""
    from takahe.models import Domain, Identity
    from takahe.utils import Takahe

    domain, _ = Domain.objects.get_or_create(
        domain=domain_name, defaults={"local": False}
    )
    identity = Identity.objects.create(
        actor_uri=f"https://{domain_name}/users/{username}/",
        profile_uri=f"https://{domain_name}/@{username}",
        local=False,
        username=username,
        domain=domain,
    )
    return Takahe.get_or_create_remote_apidentity(identity)


def _link_mastodon(
    user, handle="alice@mast.example", url="https://mast.example/@alice"
):
    """Link a Mastodon account to ``user`` so ``user.mastodon`` resolves it."""
    username, domain = handle.split("@")
    return MastodonAccount.objects.create(
        user=user,
        domain=domain,
        uid=f"uid-{handle}",
        handle=handle,
        account_data={"url": url, "username": username},
    )


def _link_bluesky(user, handle="alice.bsky.social"):
    """Link a Bluesky account to ``user`` (handle is a domain; url == https://handle)."""
    return BlueskyAccount.objects.create(
        user=user,
        domain="bsky.app",
        uid=f"did:plc:{handle}",
        handle=handle,
        account_data={},
    )


class TestMatcher:
    def test_handle_match(self):
        assert (
            match_creator_identity(
                ["hosted by @alice@example.org weekly"], ["@alice@example.org"]
            )
            == "@alice@example.org"
        )

    def test_case_insensitive(self):
        assert match_creator_identity(
            ["Contact @Alice@Example.ORG"], ["@alice@example.org"]
        )

    def test_boundary_no_overmatch(self):
        assert (
            match_creator_identity(
                ["find me at @alice@example.org.evil"], ["@alice@example.org"]
            )
            is None
        )

    def test_left_boundary_no_overmatch(self):
        assert (
            match_creator_identity(
                ["contact @x@alice@example.org here"], ["@alice@example.org"]
            )
            is None
        )
        assert (
            match_creator_identity(
                ["https://evil.example/https://example.org/@alice"],
                ["https://example.org/@alice"],
            )
            is None
        )

    def test_no_match_inside_other_urls(self):
        assert (
            match_creator_identity(
                ["https://evil.example/@alice@example.org/profile"],
                ["@alice@example.org"],
            )
            is None
        )
        assert (
            match_creator_identity(
                ["https://evil.example/redirect?u=https://example.org/@alice"],
                ["https://example.org/@alice"],
            )
            is None
        )

    def test_boundary_at_end_and_punctuation(self):
        assert match_creator_identity(["by @alice@example.org"], ["@alice@example.org"])
        assert match_creator_identity(
            ["by @alice@example.org, weekly"], ["@alice@example.org"]
        )

    def test_trailing_sentence_punctuation_matches(self):
        # a handle/url ending a sentence (followed by ".") must still match
        assert match_creator_identity(
            ["Follow me at @alice@example.org."], ["@alice@example.org"]
        )
        assert match_creator_identity(
            ["see https://example.org/@alice."], ["https://example.org/@alice"]
        )
        # but a "." that continues the token still blocks the match
        assert (
            match_creator_identity(["@alice@example.org.evil"], ["@alice@example.org"])
            is None
        )

    def test_actor_url_match(self):
        assert match_creator_identity(
            ['<a href="https://example.org/@alice">me</a>'],
            ["https://example.org/@alice"],
        )

    def test_actor_url_no_overmatch(self):
        assert (
            match_creator_identity(
                ["https://example.org/@alice2"], ["https://example.org/@alice"]
            )
            is None
        )

    def test_no_description(self):
        assert match_creator_identity([""], ["@alice@example.org"]) is None


class TestAudioEpisode:
    def test_audio_enclosure_counts(self):
        assert _has_audio_episode(
            {
                "episodes": [
                    {"enclosures": [{"url": "x.mp3", "mime_type": "audio/mpeg"}]}
                ]
            }
        )

    def test_missing_mime_is_accepted(self):
        assert _has_audio_episode({"episodes": [{"enclosures": [{"url": "x.mp3"}]}]})

    def test_no_episodes_or_enclosures(self):
        assert not _has_audio_episode({})
        assert not _has_audio_episode({"episodes": []})
        assert not _has_audio_episode({"episodes": [{"enclosures": []}]})

    def test_non_audio_and_urlless_do_not_count(self):
        assert not _has_audio_episode(
            {"episodes": [{"enclosures": [{"url": "v.mp4", "mime_type": "video/mp4"}]}]}
        )
        assert not _has_audio_episode(
            {"episodes": [{"enclosures": [{"mime_type": "audio/mpeg"}]}]}
        )


@pytest.mark.django_db(databases="__all__")
class TestCandidates:
    def test_local_identity(self):
        user = User.register(email="a@example.com", username="alice")
        candidates = creator_identity_candidates(user)
        assert f"@{user.identity.full_handle}" in candidates
        if user.identity.actor_uri:
            assert user.identity.actor_uri in candidates
        if user.identity.profile_uri:
            assert user.identity.profile_uri in candidates

    def test_linked_accounts(self, monkeypatch):
        user = User.register(email="a@example.com", username="alice")
        monkeypatch.setattr(
            user,
            "mastodon",
            SimpleNamespace(
                handle="alice@mast.example", url="https://mast.example/@alice"
            ),
            raising=False,
        )
        monkeypatch.setattr(
            user,
            "bluesky",
            SimpleNamespace(
                handle="alice.bsky.example", url="https://alice.bsky.example"
            ),
            raising=False,
        )
        candidates = creator_identity_candidates(user)
        assert "@alice@mast.example" in candidates
        assert "https://mast.example/@alice" in candidates
        assert "@alice.bsky.example" in candidates
        # a bluesky handle is a domain, so its url form is accepted too
        assert "https://alice.bsky.example" in candidates


@pytest.mark.django_db(databases="__all__")
class TestVerifyTask:
    def _claim(self, user, podcast):
        return VerifiedCreator.objects.create(item=podcast, owner=user.identity)

    def test_verified_on_match(self, monkeypatch):
        audits = _capture_discord(
            monkeypatch, "catalog.jobs.creator_verify.discord_send"
        )
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        claim = self._claim(user, podcast)
        handle = f"@{user.identity.full_handle}"
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": f"a podcast by {handle}", "episodes": _AUDIO_EPISODES},
                "",
                "",
                200,
            ),
        )
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.VERIFIED
        assert claim.matched == handle
        # a successful self-service verification posts to the audit channel
        assert len(audits) == 1
        assert audits[0][0][0] == "audit"

    def test_failed_no_match(self, monkeypatch):
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        claim = self._claim(user, podcast)
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": "no identity here", "episodes": _AUDIO_EPISODES},
                "",
                "",
                200,
            ),
        )
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.FAILED
        assert claim.failure_reason == VerifiedCreator.FailureReason.NO_MATCH

    def test_failed_no_audio_episode(self, monkeypatch):
        # a feed with no audio episode is not a podcast and must not verify,
        # even when the identity matches
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        claim = self._claim(user, podcast)
        handle = f"@{user.identity.full_handle}"
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": f"by {handle}", "episodes": []},
                "",
                "",
                200,
            ),
        )
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.FAILED
        assert claim.failure_reason == VerifiedCreator.FailureReason.NO_AUDIO

    def test_unexpected_error_fails_claim(self, monkeypatch):
        # a crash while matching must fail the claim, not leave it PENDING
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        claim = self._claim(user, podcast)
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": "x", "episodes": _AUDIO_EPISODES},
                "",
                "",
                200,
            ),
        )

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("catalog.jobs.creator_verify._match_creator", boom)
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.FAILED
        assert claim.failure_reason == VerifiedCreator.FailureReason.FETCH_FAILED

    def test_failed_fetch_error(self, monkeypatch):
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        claim = self._claim(user, podcast)
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (None, "", "", 0),
        )
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.FAILED
        assert claim.failure_reason == VerifiedCreator.FailureReason.FETCH_FAILED

    def test_failed_no_feed(self):
        user = User.register(email="a@example.com", username="alice")
        podcast = Podcast.objects.create(
            localized_title=[{"lang": "en", "text": "no feed"}]
        )
        claim = self._claim(user, podcast)
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.FAILED
        assert claim.failure_reason == VerifiedCreator.FailureReason.NO_FEED

    def test_failed_no_feed_with_rss_type_but_no_value(self):
        # feed_url must be None (not "http://None") for RSS type without value
        user = User.register(email="a@example.com", username="alice")
        podcast = Podcast.objects.create(
            localized_title=[{"lang": "en", "text": "no feed"}],
            primary_lookup_id_type=IdType.RSS,
        )
        assert podcast.feed_url is None
        claim = self._claim(user, podcast)
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.FAILED
        assert claim.failure_reason == VerifiedCreator.FailureReason.NO_FEED


@pytest.mark.django_db(databases="__all__")
class TestVerifyViews:
    def test_page_requires_login(self):
        podcast = _podcast()
        response = Client().get(f"/podcast/{podcast.uuid}/verify")
        assert response.status_code == 302

    def test_page_podcast_only(self):
        from catalog.models import Edition

        user = User.register(email="a@example.com", username="alice")
        book = Edition.objects.create(title="a book")
        response = _client(user).get(f"/book/{book.uuid}/verify")
        assert response.status_code == 400

    def test_page_shows_candidates(self):
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        response = _client(user).get(f"/podcast/{podcast.uuid}/verify")
        assert response.status_code == 200
        content = response.content.decode()
        # only link/url identifiers are listed (bare @handles are discouraged)
        url_candidates = [c for c in creator_identity_candidates(user) if "://" in c]
        assert url_candidates
        assert all(c in content for c in url_candidates)

    def test_start_flow(self, monkeypatch):
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        handle = f"@{user.identity.full_handle}"
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": f"by {handle}", "episodes": _AUDIO_EPISODES},
                "",
                "",
                200,
            ),
        )
        monkeypatch.setattr(
            "catalog.views.verify.enqueue_creator_verification",
            lambda claim, u: verify_creator_task(claim.pk, u.pk),
        )
        client = _client(user)
        response = client.post(f"/podcast/{podcast.uuid}/verify/start")
        assert response.status_code == 302
        claim = VerifiedCreator.objects.get(item=podcast, owner=user.identity)
        assert claim.state == VerifiedCreator.State.VERIFIED
        response = client.get(f"/podcast/{podcast.uuid}/verify/status")
        assert response.status_code == 200
        assert "verified creator" in response.content.decode()
        # poll concluded: client is told to reload the page
        assert response.headers.get("HX-Refresh") == "true"

    def test_start_twice_while_pending_enqueues_once(self, monkeypatch):
        # re-submitting while a verification is still pending must not enqueue
        # a duplicate job
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        calls = []
        monkeypatch.setattr(
            "catalog.views.verify.enqueue_creator_verification",
            lambda claim, u: calls.append(claim.pk),
        )
        client = _client(user)
        assert client.post(f"/podcast/{podcast.uuid}/verify/start").status_code == 302
        assert client.post(f"/podcast/{podcast.uuid}/verify/start").status_code == 302
        assert len(calls) == 1
        claim = VerifiedCreator.objects.get(item=podcast, owner=user.identity)
        assert claim.state == VerifiedCreator.State.PENDING

    def test_blocked_start_does_not_orphan_claim(self, monkeypatch):
        # if the cooldown lock blocks a submission, no PENDING claim should be
        # left behind with no job (which would wedge future retries)
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        calls = []
        monkeypatch.setattr(
            "catalog.views.verify.enqueue_creator_verification",
            lambda claim, u: calls.append(claim.pk),
        )
        client = _client(user)
        # first start acquires the cooldown lock and enqueues
        client.post(f"/podcast/{podcast.uuid}/verify/start")
        claim = VerifiedCreator.objects.get(item=podcast, owner=user.identity)
        # simulate the user removing the claim while the lock is still held
        claim.delete()
        # a resubmit within the cooldown is blocked and must NOT create a claim
        client.post(f"/podcast/{podcast.uuid}/verify/start")
        assert not VerifiedCreator.objects.filter(
            item=podcast, owner=user.identity
        ).exists()
        assert len(calls) == 1

    def test_manual_verify_staff_only(self, monkeypatch):
        audits = _capture_discord(monkeypatch, "catalog.views.verify.discord_send")
        user = User.register(email="a@example.com", username="alice")
        staff = User.register(email="root@example.com", username="root")
        podcast = _podcast()
        # a non-staff user cannot manually verify, and posts no audit
        response = _client(user).post(
            f"/podcast/{podcast.uuid}/verify/manual", {"handle": "@alice"}
        )
        assert response.status_code == 403
        assert audits == []
        # staff (not superuser) can manually verify
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        response = _client(staff).post(
            f"/podcast/{podcast.uuid}/verify/manual", {"handle": "@alice"}
        )
        assert response.status_code == 302
        claim = VerifiedCreator.objects.get(item=podcast, owner=user.identity)
        assert claim.state == VerifiedCreator.State.VERIFIED
        assert claim.matched == "manual"
        assert len(audits) == 1
        assert audits[0][0][0] == "audit"

    def test_manual_verify_superuser_still_allowed(self, monkeypatch):
        # is_staff and is_superuser are independent flags here; broadening to
        # staff must not drop access for a superuser who is not also staff
        _capture_discord(monkeypatch, "catalog.views.verify.discord_send")
        user = User.register(email="a@example.com", username="alice")
        admin = User.register(email="root@example.com", username="root")
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])
        assert not admin.is_staff
        podcast = _podcast()
        response = _client(admin).post(
            f"/podcast/{podcast.uuid}/verify/manual", {"handle": "@alice"}
        )
        assert response.status_code == 302
        claim = VerifiedCreator.objects.get(item=podcast, owner=user.identity)
        assert claim.state == VerifiedCreator.State.VERIFIED

    def test_unverify_permissions(self, monkeypatch):
        audits = _capture_discord(monkeypatch, "catalog.views.verify.discord_send")
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        staff = User.register(email="root@example.com", username="root")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        podcast = _podcast()
        claim = _verified(podcast, alice.identity)
        url = f"/podcast/{podcast.uuid}/verify/remove"
        # an unrelated non-staff user cannot remove, and posts no audit
        response = _client(bob).post(url, {"claim_id": claim.pk})
        assert response.status_code == 403
        assert audits == []
        # staff (not superuser) can remove anyone's claim
        response = _client(staff).post(url, {"claim_id": claim.pk})
        assert response.status_code == 302
        assert not VerifiedCreator.objects.filter(pk=claim.pk).exists()
        # the creator can still remove their own claim
        claim = _verified(podcast, alice.identity)
        response = _client(alice).post(url, {"claim_id": claim.pk})
        assert response.status_code == 302
        assert not VerifiedCreator.objects.filter(pk=claim.pk).exists()
        # both successful removals posted to the audit channel
        assert len(audits) == 2
        assert all(c[0][0] == "audit" for c in audits)

    def test_unverify_invalid_claim_id(self):
        # a missing or non-numeric claim_id is a bad request, not a 500
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        url = f"/podcast/{podcast.uuid}/verify/remove"
        assert _client(user).post(url, {"claim_id": "abc"}).status_code == 400
        assert _client(user).post(url, {}).status_code == 400


@pytest.mark.django_db(databases="__all__")
class TestEditPermissions:
    def test_edit_locked_to_creator(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        staff = User.register(email="s@example.com", username="staff")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        podcast = _podcast()
        url = f"/podcast/{podcast.uuid}/edit"
        # before verification anyone may edit
        assert _client(bob).get(url).status_code == 200
        _verified(podcast, alice.identity)
        assert _client(bob).get(url).status_code == 403
        assert _client(alice).get(url).status_code == 200
        assert _client(staff).get(url).status_code == 200

    def test_pending_claim_does_not_lock(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast()
        VerifiedCreator.objects.create(item=podcast, owner=alice.identity)
        assert _client(bob).get(f"/podcast/{podcast.uuid}/edit").status_code == 200

    def test_protected_trumps_creator(self):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        podcast.is_protected = True
        podcast.save(update_fields=["is_protected"])
        assert _client(alice).get(f"/podcast/{podcast.uuid}/edit").status_code == 403

    def test_merge_into_verified_item_blocked(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        target = _podcast("Target", "target.example.com/feed.rss")
        source = _podcast("Source", "source.example.com/feed.rss")
        _verified(target, alice.identity)
        response = _client(bob).post(
            f"/podcast/{source.uuid}/merge",
            {"target_item_url": target.absolute_url, "sure": "1"},
        )
        assert response.status_code == 403

    def test_episode_edit_inherits_parent_lock(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast()
        episode = PodcastEpisode.objects.create(
            program=podcast,
            guid="guid-lock-1",
            title="ep",
            pub_date=timezone.now(),
            media_url="https://example.com/1.mp3",
        )
        _verified(podcast, alice.identity)
        url = f"/podcast/episode/{episode.uuid}/edit"
        assert _client(bob).get(url).status_code == 403
        assert _client(alice).get(url).status_code == 200

    def test_credit_edit_blocked_for_non_creator(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        response = _client(bob).post(
            f"/podcast/{podcast.uuid}/credits/add", {"role": "host", "name": "Bob"}
        )
        assert response.status_code == 403
        response = _client(alice).post(
            f"/podcast/{podcast.uuid}/credits/add", {"role": "host", "name": "Alice"}
        )
        assert response.status_code == 200

    def test_create_child_blocked_for_non_creator(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        response = _client(bob).post(
            f"/catalog/create/PodcastEpisode?parent={podcast.uuid}"
        )
        assert response.status_code == 403

    def test_refetch_blocked_for_non_creator(self, monkeypatch):
        from catalog.models import ExternalResource
        from catalog.sites import SiteManager

        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast()
        url = "https://podcast.example.com/feed.rss"
        ExternalResource.objects.create(
            item=podcast,
            id_type=IdType.RSS,
            id_value="podcast.example.com/feed.rss",
            url=url,
        )
        _verified(podcast, alice.identity)
        # is_valid_url resolves DNS, which fails for the fake test hostname
        monkeypatch.setattr(
            SiteManager,
            "get_site_by_url",
            staticmethod(
                lambda u, detect_redirection=True, detect_fallback=True: RSS(u)
            ),
        )
        response = _client(bob).post("/refetch", {"url": url})
        assert response.status_code == 403
        # scheme variant of the stored url resolves to the same resource and
        # must not bypass the check
        response = _client(bob).post(
            "/refetch", {"url": url.replace("https://", "http://")}
        )
        assert response.status_code == 403


@pytest.mark.django_db(databases="__all__")
class TestMergeTransfer:
    def test_claims_removed_on_merge(self):
        # claims prove ownership of the source feed, not the target, so they
        # must not transfer: otherwise verifying a throwaway feed and merging
        # it into an unrelated item would grant creator control over it
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        source = _podcast("Source", "source.example.com/feed.rss")
        target = _podcast("Target", "target.example.com/feed.rss")
        _verified(source, alice.identity)
        _verified(target, bob.identity)
        source.merge_to(target)
        owners = set(target.verified_creators.values_list("owner_id", flat=True))
        assert owners == {bob.identity.pk}
        assert not source.verified_creators.exists()


@pytest.mark.django_db(databases="__all__")
class TestProfileAndWorksPage:
    def test_has_verified_works(self):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        assert not alice.identity.has_verified_works
        claim = VerifiedCreator.objects.create(item=podcast, owner=alice.identity)
        assert not alice.identity.has_verified_works  # pending does not count
        claim.state = VerifiedCreator.State.VERIFIED
        claim.save()
        assert alice.identity.has_verified_works

    def test_verified_works_page(self):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast("My Show")
        _verified(podcast, alice.identity)
        response = Client().get("/users/alice/verified_works")
        assert response.status_code == 200
        assert "My Show" in response.content.decode()


@pytest.mark.django_db(databases="__all__")
class TestOriginalEpisodes:
    def _episodes(self, podcast, n=3):
        return [
            PodcastEpisode.objects.create(
                program=podcast,
                guid=f"guid-{podcast.pk}-{i}",
                title=f"ep {i}",
                pub_date=timezone.now() - timedelta(days=i),
                media_url="https://example.com/1.mp3",
            )
            for i in range(n)
        ]

    def test_only_verified_podcasts_included(self):
        alice = User.register(email="a@example.com", username="alice")
        verified_pod = _podcast("Verified", "v.example.com/feed.rss")
        other_pod = _podcast("Other", "o.example.com/feed.rss")
        _verified(verified_pod, alice.identity)
        self._episodes(verified_pod)
        self._episodes(other_pod)
        episodes = DiscoverGenerator().get_original_episodes()
        assert episodes
        assert {e.program_id for e in episodes} == {verified_pod.pk}
        dates = [e.pub_date for e in episodes]
        assert dates == sorted(dates, reverse=True)

    def test_program_host_credits_prefetched_no_n_plus_one(self):
        """NEODB-SOCIAL-7NC: discover episode cards read
        ``item.program.host_names`` (host credits), so ``get_original_episodes``
        must prefetch each program's credits; reading them across N episodes
        must not fire one ``catalog_itemcredit`` query per episode.
        """
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        ItemCredit.objects.create(item=podcast, role="host", name="Alice Host")
        self._episodes(podcast, n=3)
        episodes = DiscoverGenerator().get_original_episodes()
        assert len(episodes) == 3
        with CaptureQueriesContext(connection) as ctx:
            names = [e.program.host_names for e in episodes]
        assert names == [["Alice Host"]] * 3
        offending = [
            q for q in ctx.captured_queries if "catalog_itemcredit" in q["sql"]
        ]
        assert offending == [], (
            f"reading program.host_names fired {len(offending)} catalog_itemcredit "
            f"query(ies); program credits should be prefetched. First: "
            f"{offending[0]['sql'] if offending else 'n/a'}"
        )

    def test_no_duplicate_episodes_with_multiple_verified_creators(self):
        # a show with several verified creators must surface each episode once,
        # not once per verified-creator claim
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        _verified(podcast, bob.identity)
        self._episodes(podcast)
        episodes = DiscoverGenerator().get_original_episodes()
        uuids = [e.uuid for e in episodes]
        assert len(uuids) == len(set(uuids))
        assert {e.program_id for e in episodes} == {podcast.pk}

    def test_pending_claims_excluded(self):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        VerifiedCreator.objects.create(item=podcast, owner=alice.identity)
        self._episodes(podcast)
        assert DiscoverGenerator().get_original_episodes() == []

    def test_restricted_creator_episodes_excluded(self):
        # episodes of a show whose verified creator is restricted must not
        # surface in the discover "original episodes" shelf
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        kept_pod = _podcast("Kept", "k.example.com/feed.rss")
        hidden_pod = _podcast("Hidden", "h.example.com/feed.rss")
        _verified(kept_pod, alice.identity)
        _verified(hidden_pod, bob.identity)
        self._episodes(kept_pod)
        self._episodes(hidden_pod)
        _restrict(bob.identity, level=2)
        episodes = DiscoverGenerator().get_original_episodes()
        assert episodes
        assert {e.program_id for e in episodes} == {kept_pod.pk}

    def _episode(self, podcast, guid, days_ago):
        return PodcastEpisode.objects.create(
            program=podcast,
            guid=guid,
            title=guid,
            pub_date=timezone.now() - timedelta(days=days_ago),
            media_url="https://example.com/1.mp3",
        )

    def test_interleaved_so_each_podcast_gets_exposure(self):
        # pod A has many recent episodes, pod B has a single older one;
        # round-robin still surfaces B at the second slot.
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        pod_a = _podcast("A", "a.example.com/feed.rss")
        pod_b = _podcast("B", "b.example.com/feed.rss")
        _verified(pod_a, alice.identity)
        _verified(pod_b, bob.identity)
        for i in range(5):
            self._episode(pod_a, f"a{i}", days_ago=i)
        self._episode(pod_b, "b0", days_ago=100)
        episodes = DiscoverGenerator().get_original_episodes()
        programs = [e.program_id for e in episodes]
        assert programs[:2] == [pod_a.pk, pod_b.pk]
        assert len(episodes) == 6

    def test_max_per_program(self):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        for i in range(15):
            self._episode(podcast, f"e{i}", days_ago=i)
        episodes = DiscoverGenerator().get_original_episodes()
        assert len(episodes) == 10
        # the 10 newest are kept, ordered newest first
        assert [e.guid for e in episodes] == [f"e{i}" for i in range(10)]

    def test_max_items_total(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        pod_a = _podcast("A", "a.example.com/feed.rss")
        pod_b = _podcast("B", "b.example.com/feed.rss")
        _verified(pod_a, alice.identity)
        _verified(pod_b, bob.identity)
        for i in range(3):
            self._episode(pod_a, f"a{i}", days_ago=i)
            self._episode(pod_b, f"b{i}", days_ago=i)
        episodes = DiscoverGenerator().get_original_episodes(max_items=3)
        assert len(episodes) == 3

    def test_discover_gated_by_site_setting(self, monkeypatch):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        episodes = self._episodes(podcast)
        cache.set(
            "public_gallery",
            [{"name": "original_episodes", "category": podcast.category}],
            timeout=None,
        )
        cache.set("original_episodes", episodes, timeout=None)
        # Pin config so the refresh middleware does not reload it mid-test.
        monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
        # Off by default: shelf hidden.
        monkeypatch.setattr(SiteConfig.system, "discover_show_verified_podcasts", False)
        response = Client().get("/discover/")
        assert 'id="original_episodes"' not in response.content.decode()
        # Enabled: shelf shown, even to anonymous viewers.
        monkeypatch.setattr(SiteConfig.system, "discover_show_verified_podcasts", True)
        response = Client().get("/discover/")
        assert 'id="original_episodes"' in response.content.decode()
        monkeypatch.setattr(SiteConfig.system, "discover_show_verified_podcasts", False)
        response = Client().get("/discover/")
        assert 'id="original_episodes"' not in response.content.decode()


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_original_episodes_api(live_server):
    import requests

    alice = User.register(email="a@example.com", username="alice")
    podcast = _podcast()
    _verified(podcast, alice.identity)
    episode = PodcastEpisode.objects.create(
        program=podcast,
        guid="guid-api-1",
        title="api ep",
        pub_date=timezone.now(),
        media_url="https://example.com/1.mp3",
    )
    cache.set("original_episodes", [episode], timeout=None)
    response = requests.get(
        f"{live_server.url}/api/trending/podcast/original/", timeout=5
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["uuid"] == episode.uuid


@pytest.mark.django_db(databases="__all__")
class TestVerifiedOriginals:
    def test_only_verified_podcasts(self):
        alice = User.register(email="a@example.com", username="alice")
        verified_pod = _podcast("Verified", "v.example.com/feed.rss")
        pending_pod = _podcast("Pending", "p.example.com/feed.rss")
        plain_pod = _podcast("Plain", "x.example.com/feed.rss")
        _verified(verified_pod, alice.identity)
        VerifiedCreator.objects.create(item=pending_pod, owner=alice.identity)
        assert list(Podcast.verified_originals()) == [verified_pod]
        assert plain_pod not in Podcast.verified_originals()

    def test_deleted_or_merged_excluded(self):
        alice = User.register(email="a@example.com", username="alice")
        deleted_pod = _podcast("Deleted", "d.example.com/feed.rss")
        live_pod = _podcast("Live", "l.example.com/feed.rss")
        _verified(deleted_pod, alice.identity)
        _verified(live_pod, alice.identity)
        deleted_pod.is_deleted = True
        deleted_pod.save()
        assert list(Podcast.verified_originals()) == [live_pod]

    def test_distinct_with_multiple_claims(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        _verified(podcast, bob.identity)
        assert list(Podcast.verified_originals()) == [podcast]

    def test_blocked_creator_excluded(self):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        _restrict(alice.identity, level=2)
        assert list(Podcast.verified_originals()) == []

    def test_limited_creator_excluded(self):
        # the discover convention hides limited identities too, not just blocked
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        _restrict(alice.identity, level=1)
        assert list(Podcast.verified_originals()) == []

    def test_any_restricted_creator_hides_show(self):
        # a show co-hosted by a restricted creator is hidden entirely, even
        # though it still has an unrestricted verified creator
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast()
        _verified(podcast, alice.identity)
        _verified(podcast, bob.identity)
        _restrict(bob.identity, level=2)
        assert list(Podcast.verified_originals()) == []

    def test_unrestricted_creator_kept(self):
        # restricting an unrelated creator must not drop other shows
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        alice_pod = _podcast("Alice", "a.example.com/feed.rss")
        bob_pod = _podcast("Bob", "b.example.com/feed.rss")
        _verified(alice_pod, alice.identity)
        _verified(bob_pod, bob.identity)
        _restrict(bob.identity, level=2)
        assert list(Podcast.verified_originals()) == [alice_pod]

    def test_ordered_by_most_recently_verified(self):
        alice = User.register(email="a@example.com", username="alice")
        pod_old = _podcast("Old", "old.example.com/feed.rss")
        pod_new = _podcast("New", "new.example.com/feed.rss")
        claim_old = _verified(pod_old, alice.identity)
        claim_new = _verified(pod_new, alice.identity)
        now = timezone.now()
        VerifiedCreator.objects.filter(pk=claim_old.pk).update(
            created_time=now - timedelta(days=2)
        )
        VerifiedCreator.objects.filter(pk=claim_new.pk).update(created_time=now)
        assert list(Podcast.verified_originals()) == [pod_new, pod_old]

    def test_page_lists_verified_podcasts(self):
        alice = User.register(email="a@example.com", username="alice")
        verified_pod = _podcast("My Verified Show", "v.example.com/feed.rss")
        _podcast("Just A Show", "x.example.com/feed.rss")
        _verified(verified_pod, alice.identity)
        response = Client().get("/discover/original-podcasts/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "My Verified Show" in body
        assert "Just A Show" not in body


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_verified_podcasts_api(live_server):
    import requests

    alice = User.register(email="a@example.com", username="alice")
    podcast = _podcast("Api Show")
    _verified(podcast, alice.identity)
    response = requests.get(
        f"{live_server.url}/api/trending/podcast/verified/", timeout=5
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["pages"] == 1
    assert len(payload["data"]) == 1
    assert payload["data"][0]["uuid"] == podcast.uuid


@pytest.mark.django_db(databases="__all__")
class TestMastodonAttribution:
    def test_resolve_identity_local(self):
        user = User.register(email="a@example.com", username="alice")
        local = f"@{user.identity.full_handle}"
        assert resolve_creator_identity(user, local) == user.identity

    def test_resolve_identity_mastodon(self):
        user = User.register(email="a@example.com", username="alice")
        _link_mastodon(user)
        remote = _make_remote_identity("alice", "mast.example")
        # a match against the linked Mastodon handle/url resolves to its
        # remote identity; the local handle stays the local identity
        assert resolve_creator_identity(user, "@alice@mast.example") == remote
        assert resolve_creator_identity(user, "https://mast.example/@alice") == remote
        assert resolve_creator_identity(user, f"@{user.identity.full_handle}") == (
            user.identity
        )

    def test_user_controls_owner(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        _link_mastodon(alice)
        remote = _make_remote_identity("alice", "mast.example")
        # local identity and the linked Mastodon identity are both controlled
        assert user_controls_owner(alice, alice.identity)
        assert user_controls_owner(alice, remote)
        # someone else's identities are not
        assert not user_controls_owner(bob, remote)
        assert not user_controls_owner(bob, alice.identity)

    def test_user_owned_claims_q(self):
        alice = User.register(email="a@example.com", username="alice")
        _link_mastodon(alice)
        remote = _make_remote_identity("alice", "mast.example")
        podcast = _podcast()
        local_claim = _verified(podcast, alice.identity)
        other = _podcast("Other", "other.example.com/feed.rss")
        remote_claim = _verified(other, remote, matched="@alice@mast.example")
        owned = set(
            VerifiedCreator.objects.filter(user_owned_claims_q(alice)).values_list(
                "pk", flat=True
            )
        )
        assert owned == {local_claim.pk, remote_claim.pk}

    def test_verify_rehomes_to_mastodon(self, monkeypatch):
        user = User.register(email="a@example.com", username="alice")
        _link_mastodon(user)
        remote = _make_remote_identity("alice", "mast.example")
        podcast = _podcast()
        claim = VerifiedCreator.objects.create(item=podcast, owner=user.identity)
        handle = "@alice@mast.example"
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": f"hosted by {handle}", "episodes": _AUDIO_EPISODES},
                "",
                "",
                200,
            ),
        )
        verify_creator_task(claim.pk, user.pk)
        # the pending local claim is re-homed onto the linked Mastodon identity
        assert not VerifiedCreator.objects.filter(pk=claim.pk).exists()
        assert not VerifiedCreator.objects.filter(
            item=podcast, owner=user.identity
        ).exists()
        rehomed = VerifiedCreator.objects.get(item=podcast, owner=remote)
        assert rehomed.state == VerifiedCreator.State.VERIFIED
        assert rehomed.matched == handle

    def test_rehome_onto_existing_claim_refreshes_edited_time(self, monkeypatch):
        # re-homing onto an already-existing mastodon-owned claim must refresh
        # edited_time (the page orders "my claim" by -edited_time)
        user = User.register(email="a@example.com", username="alice")
        _link_mastodon(user)
        remote = _make_remote_identity("alice", "mast.example")
        podcast = _podcast()
        existing = _verified(podcast, remote, matched="@alice@mast.example")
        old = timezone.now() - timedelta(days=3)
        VerifiedCreator.objects.filter(pk=existing.pk).update(edited_time=old)
        claim = VerifiedCreator.objects.create(item=podcast, owner=user.identity)
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {
                    "description": "by @alice@mast.example",
                    "episodes": _AUDIO_EPISODES,
                },
                "",
                "",
                200,
            ),
        )
        verify_creator_task(claim.pk, user.pk)
        existing.refresh_from_db()
        assert existing.state == VerifiedCreator.State.VERIFIED
        assert existing.edited_time > old

    def test_unverify_mastodon_owned_claim(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        _link_mastodon(alice)
        remote = _make_remote_identity("alice", "mast.example")
        podcast = _podcast()
        claim = _verified(podcast, remote, matched="@alice@mast.example")
        url = f"/podcast/{podcast.uuid}/verify/remove"
        # bob does not control the Mastodon identity
        assert _client(bob).post(url, {"claim_id": claim.pk}).status_code == 403
        # alice does, via her linked Mastodon account
        assert _client(alice).post(url, {"claim_id": claim.pk}).status_code == 302
        assert not VerifiedCreator.objects.filter(pk=claim.pk).exists()

    def test_edit_allowed_for_mastodon_attributed_creator(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        _link_mastodon(alice)
        remote = _make_remote_identity("alice", "mast.example")
        podcast = _podcast()
        _verified(podcast, remote, matched="@alice@mast.example")
        url = f"/podcast/{podcast.uuid}/edit"
        assert _client(bob).get(url).status_code == 403
        assert _client(alice).get(url).status_code == 200


@pytest.mark.django_db(databases="__all__")
class TestFeedLinkCreator:
    def _feed(self, monkeypatch, description, link="https://pod.example/"):
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": description, "link": link, "episodes": _AUDIO_EPISODES},
                "",
                "",
                200,
            ),
        )

    def _rel_me(self, monkeypatch, urls):
        monkeypatch.setattr(
            "catalog.jobs.creator_verify._fetch_page_rel_me_urls",
            lambda url: urls,
        )

    def test_page_rel_me_preferred_over_description(self, monkeypatch):
        # a rel="me" to the linked Mastodon wins over a description match and
        # attributes the work to that Mastodon identity
        user = User.register(email="a@example.com", username="alice")
        _link_mastodon(user)
        remote = _make_remote_identity("alice", "mast.example")
        podcast = _podcast()
        claim = VerifiedCreator.objects.create(item=podcast, owner=user.identity)
        self._feed(monkeypatch, f"by @{user.identity.full_handle}")
        self._rel_me(monkeypatch, ["https://mast.example/@alice"])
        verify_creator_task(claim.pk, user.pk)
        rehomed = VerifiedCreator.objects.get(item=podcast, owner=remote)
        assert rehomed.matched == "https://mast.example/@alice"

    def test_page_rel_me_matches_bluesky(self, monkeypatch):
        # a rel="me" to the user's Bluesky also verifies (stays local identity)
        user = User.register(email="a@example.com", username="alice")
        _link_bluesky(user, "alice.bsky.social")
        podcast = _podcast()
        claim = VerifiedCreator.objects.create(item=podcast, owner=user.identity)
        self._feed(monkeypatch, "no identity in here")
        self._rel_me(monkeypatch, ["https://alice.bsky.social"])
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.VERIFIED
        assert claim.matched == "https://alice.bsky.social"
        assert claim.owner_id == user.identity.pk

    def test_channel_link_is_bluesky_handle(self, monkeypatch):
        # the channel link being on the user's Bluesky handle (a domain) passes
        # even with no rel="me" on the page
        user = User.register(email="a@example.com", username="alice")
        _link_bluesky(user, "alice.bsky.social")
        podcast = _podcast()
        claim = VerifiedCreator.objects.create(item=podcast, owner=user.identity)
        self._feed(monkeypatch, "nothing here", link="https://alice.bsky.social/show")
        self._rel_me(monkeypatch, [])
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.VERIFIED
        assert claim.matched == "@alice.bsky.social"
        assert claim.owner_id == user.identity.pk

    def test_unrelated_rel_me_falls_back_to_description(self, monkeypatch):
        # rel="me" links that aren't the user's are ignored; we fall back to the
        # description match against the user's own candidates
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        claim = VerifiedCreator.objects.create(item=podcast, owner=user.identity)
        local_handle = f"@{user.identity.full_handle}"
        self._feed(monkeypatch, f"by {local_handle}")
        self._rel_me(monkeypatch, ["https://stranger.example/@bob"])
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.VERIFIED
        assert claim.matched == local_handle
        assert claim.owner_id == user.identity.pk


@pytest.mark.django_db(databases="__all__")
class TestItemPageMeta:
    def test_fediverse_creator_meta_on_item_page(self):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast("Meta Show")
        _verified(podcast, alice.identity)
        response = Client().get(podcast.url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="fediverse:creator"' in content
        assert f"@{alice.identity.full_handle}" in content

    def test_meta_emitted_for_each_verified_creator(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        podcast = _podcast("Co-hosted Show")
        _verified(podcast, alice.identity)
        _verified(podcast, bob.identity)
        content = Client().get(podcast.url).content.decode()
        # one fediverse:creator meta per verified creator
        assert content.count('name="fediverse:creator"') == 2
        assert f"@{alice.identity.full_handle}" in content
        assert f"@{bob.identity.full_handle}" in content

    def test_rel_me_link_points_to_creator_profile(self):
        # a rel="me" link back to the creator's profile lets Mastodon show its
        # green "verified link" check when the creator lists this page
        remote = _make_remote_identity("alice", "mast.example")
        podcast = _podcast("Rel Me Show")
        _verified(podcast, remote, matched="@alice@mast.example")
        content = Client().get(podcast.url).content.decode()
        assert remote.profile_uri == "https://mast.example/@alice"
        assert 'rel="me"' in content
        assert remote.profile_uri in content


@pytest.mark.django_db(databases="__all__")
class TestTrendingArticles:
    def _article_post_ids(self, article) -> set:
        # read post ids from the journal side (the through table), mirroring the
        # discover job; ``article.posts`` would force a cross-database join.
        return {
            pk
            for pk in Article.objects.filter(pk=article.pk).values_list(
                "posts", flat=True
            )
            if pk is not None
        }

    def test_article_only_trends_when_author_has_shelf_items(self, monkeypatch):
        # an author's article is eligible for trending only if the author has
        # at least one item on a shelf; otherwise it is excluded.
        monkeypatch.setattr(SiteConfig.system, "discover_show_popular_posts", True)

        with_shelf = User.register(email="ws@example.com", username="hasshelf")
        without_shelf = User.register(email="ns@example.com", username="noshelf")

        book = Edition.objects.create(title="Shelf Book")
        Mark(with_shelf.identity, book).update(ShelfType.WISHLIST)
        kept = Article.update_local_article(
            owner=with_shelf.identity,
            title="Kept Article",
            body="Body of the kept article.",
            visibility=0,
        )
        dropped = Article.update_local_article(
            owner=without_shelf.identity,
            title="Dropped Article",
            body="Body of the dropped article.",
            visibility=0,
        )

        kept_posts = self._article_post_ids(kept)
        dropped_posts = self._article_post_ids(dropped)
        assert kept_posts and dropped_posts  # both articles created posts

        DiscoverGenerator().run()
        popular = set(cache.get("popular_posts") or [])

        assert kept_posts & popular  # author with a shelf item trends
        assert not (dropped_posts & popular)  # author without one does not
