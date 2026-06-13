from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from catalog.jobs.creator_verify import verify_creator_task
from catalog.jobs.discover import DiscoverGenerator
from catalog.models import (
    IdType,
    Podcast,
    PodcastEpisode,
    VerifiedCreator,
    creator_identity_candidates,
    match_creator_identity,
)
from catalog.sites.rss import RSS
from users.models import User

_BACKEND = "mastodon.auth.OAuth2Backend"


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
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        claim = self._claim(user, podcast)
        handle = f"@{user.identity.full_handle}"
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": f"a podcast by {handle}"},
                "",
                "",
                200,
            ),
        )
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.VERIFIED
        assert claim.matched == handle

    def test_failed_no_match(self, monkeypatch):
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        claim = self._claim(user, podcast)
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": "no identity here"},
                "",
                "",
                200,
            ),
        )
        verify_creator_task(claim.pk, user.pk)
        claim.refresh_from_db()
        assert claim.state == VerifiedCreator.State.FAILED
        assert claim.failure_reason == VerifiedCreator.FailureReason.NO_MATCH

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
        assert f"@{user.identity.full_handle}" in response.content.decode()

    def test_start_flow(self, monkeypatch):
        user = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        handle = f"@{user.identity.full_handle}"
        monkeypatch.setattr(
            RSS,
            "fetch_feed_with_metadata",
            lambda url, etag="", last_modified="": (
                {"description": f"by {handle}"},
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

    def test_manual_verify_superuser_only(self):
        user = User.register(email="a@example.com", username="alice")
        admin = User.register(email="root@example.com", username="root")
        podcast = _podcast()
        response = _client(user).post(
            f"/podcast/{podcast.uuid}/verify/manual", {"handle": "@alice"}
        )
        assert response.status_code == 403
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])
        response = _client(admin).post(
            f"/podcast/{podcast.uuid}/verify/manual", {"handle": "@alice"}
        )
        assert response.status_code == 302
        claim = VerifiedCreator.objects.get(item=podcast, owner=user.identity)
        assert claim.state == VerifiedCreator.State.VERIFIED
        assert claim.matched == "manual"

    def test_unverify_permissions(self):
        alice = User.register(email="a@example.com", username="alice")
        bob = User.register(email="b@example.com", username="bob")
        admin = User.register(email="root@example.com", username="root")
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])
        podcast = _podcast()
        claim = _verified(podcast, alice.identity)
        url = f"/podcast/{podcast.uuid}/verify/remove"
        response = _client(bob).post(url, {"claim_id": claim.pk})
        assert response.status_code == 403
        response = _client(admin).post(url, {"claim_id": claim.pk})
        assert response.status_code == 302
        assert not VerifiedCreator.objects.filter(pk=claim.pk).exists()
        claim = _verified(podcast, alice.identity)
        response = _client(alice).post(url, {"claim_id": claim.pk})
        assert response.status_code == 302
        assert not VerifiedCreator.objects.filter(pk=claim.pk).exists()


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

    def test_pending_claims_excluded(self):
        alice = User.register(email="a@example.com", username="alice")
        podcast = _podcast()
        VerifiedCreator.objects.create(item=podcast, owner=alice.identity)
        self._episodes(podcast)
        assert DiscoverGenerator().get_original_episodes() == []

    def test_discover_gated_by_test_enabled(self, monkeypatch):
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
        response = Client().get("/discover/")
        assert 'id="original_episodes"' not in response.content.decode()
        monkeypatch.setattr(type(alice), "test_enabled", property(lambda s: True))
        response = _client(alice).get("/discover/")
        assert 'id="original_episodes"' in response.content.decode()
        monkeypatch.setattr(type(alice), "test_enabled", property(lambda s: False))
        response = _client(alice).get("/discover/")
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
