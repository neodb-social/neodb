from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied, RequestAborted
from django.urls import reverse
from django.utils import timezone

from atproto_client.request import exceptions as atproto_exceptions
from catalog.models import Edition
from journal.models import Comment, CrosspostRetry, Piece
from mastodon.models import BlueskyAccount, MastodonAccount, ThreadsAccount
from users.jobs.cleanup import prune_crosspost_retries
from users.models import User


@pytest.fixture
def user():
    return User.register(email="cp@example.com", username="cpuser")


@pytest.fixture
def comment(user):
    book = Edition.objects.create(title="Test Book")
    return Comment.objects.create(
        owner=user.identity, item=book, text="test comment", visibility=0
    )


def _link_mastodon(user, repost_mode=1):
    user.preference.mastodon_repost_mode = repost_mode
    user.preference.save(update_fields=["mastodon_repost_mode"])
    return MastodonAccount.objects.create(
        handle="cpuser@mast.social", user=user, domain="mast.social", uid="1"
    )


def _link_threads(user):
    return ThreadsAccount.objects.create(
        handle="cpuser", user=user, domain="threads.net", uid="2"
    )


def _link_bluesky(user):
    return BlueskyAccount.objects.create(
        handle="cpuser.bsky.social", user=user, domain="bsky.social", uid="3"
    )


@pytest.mark.django_db(databases="__all__")
class TestCrosspostRetryRecording:
    def test_mastodon_auth_failure_recorded(self, user, comment, monkeypatch):
        _link_mastodon(user)

        def fail(self, **kwargs):
            raise PermissionDenied()

        monkeypatch.setattr(MastodonAccount, "post", fail)
        comment._sync_to_social_accounts(0)
        failure = CrosspostRetry.objects.get(piece=comment, platform="mastodon")
        assert failure.user == user
        assert failure.error_type == CrosspostRetry.ErrorType.auth
        assert failure.state == CrosspostRetry.State.failed

    def test_mastodon_request_failure_recorded(self, user, comment, monkeypatch):
        _link_mastodon(user)

        def fail(self, **kwargs):
            raise RequestAborted()

        monkeypatch.setattr(MastodonAccount, "post", fail)
        comment._sync_to_social_accounts(0)
        failure = CrosspostRetry.objects.get(piece=comment, platform="mastodon")
        assert failure.error_type == CrosspostRetry.ErrorType.other

    def test_success_clears_failure(self, user, comment, monkeypatch):
        _link_mastodon(user)
        CrosspostRetry.objects.create(
            user=user, piece=comment, platform="mastodon"
        )

        def ok(self, **kwargs):
            return {"id": "123", "url": "https://mast.social/@cpuser/123"}

        monkeypatch.setattr(MastodonAccount, "post", ok)
        comment._sync_to_social_accounts(0)
        assert not CrosspostRetry.objects.filter(piece=comment).exists()
        comment.refresh_from_db()
        assert comment.metadata["mastodon_id"] == "123"

    def test_repeated_failures_keep_single_row(self, user, comment, monkeypatch):
        _link_mastodon(user)

        def fail(self, **kwargs):
            raise RequestAborted()

        monkeypatch.setattr(MastodonAccount, "post", fail)
        comment._sync_to_social_accounts(0)
        comment._sync_to_social_accounts(0)
        assert (
            CrosspostRetry.objects.filter(piece=comment, platform="mastodon").count()
            == 1
        )

    def test_boost_failure_recorded_and_cleared(self, user, comment, monkeypatch):
        _link_mastodon(user, repost_mode=0)
        monkeypatch.setattr(
            Comment,
            "latest_post",
            SimpleNamespace(url="https://example.org/p/1"),
        )
        monkeypatch.setattr(MastodonAccount, "boost", lambda self, url: False)
        comment._sync_to_social_accounts(0)
        failure = CrosspostRetry.objects.get(piece=comment, platform="mastodon")
        assert failure.error_type == CrosspostRetry.ErrorType.other

        monkeypatch.setattr(MastodonAccount, "boost", lambda self, url: True)
        comment._sync_to_social_accounts(0)
        assert not CrosspostRetry.objects.filter(piece=comment).exists()

    def test_bluesky_auth_failure_recorded(self, user, comment, monkeypatch):
        _link_bluesky(user)

        def fail(self, **kwargs):
            raise atproto_exceptions.UnauthorizedError(None)

        monkeypatch.setattr(BlueskyAccount, "post", fail)
        comment._sync_to_social_accounts(0)
        failure = CrosspostRetry.objects.get(piece=comment, platform="bluesky")
        assert failure.error_type == CrosspostRetry.ErrorType.auth

    def test_threads_failure_recorded(self, user, comment, monkeypatch):
        _link_threads(user)

        def fail(self, **kwargs):
            raise RequestAborted()

        monkeypatch.setattr(ThreadsAccount, "post", fail)
        comment._sync_to_social_accounts(0)
        failure = CrosspostRetry.objects.get(piece=comment, platform="threads")
        assert failure.error_type == CrosspostRetry.ErrorType.other

    def test_platforms_filter(self, user, comment, monkeypatch):
        _link_mastodon(user)
        _link_threads(user)
        called = []

        def mastodon_post(self, **kwargs):
            called.append("mastodon")
            return {"id": "1", "url": "u"}

        def threads_post(self, **kwargs):
            called.append("threads")
            return {"id": "2"}

        monkeypatch.setattr(MastodonAccount, "post", mastodon_post)
        monkeypatch.setattr(ThreadsAccount, "post", threads_post)

        comment._sync_to_social_accounts(0, ["mastodon"])
        assert called == ["mastodon"]

        called.clear()
        comment._sync_to_social_accounts(0)
        assert sorted(called) == ["mastodon", "threads"]


@pytest.mark.django_db(databases="__all__")
class TestCrosspostViews:
    def test_page_renders(self, user, comment, client):
        CrosspostRetry.objects.create(user=user, piece=comment, platform="mastodon")
        client.force_login(user)
        response = client.get(reverse("users:crossposts"))
        assert response.status_code == 200
        assert b"Test Book" in response.content

    def test_retry(self, user, comment, client, monkeypatch):
        _link_mastodon(user)
        failure = CrosspostRetry.objects.create(
            user=user, piece=comment, platform="mastodon"
        )
        calls = []
        monkeypatch.setattr(
            Piece,
            "sync_to_social_accounts",
            lambda self, update_mode=0, platforms=None: calls.append(
                (update_mode, platforms)
            ),
        )
        client.force_login(user)
        response = client.post(reverse("users:crosspost_retry", args=[failure.pk]))
        assert response.status_code == 200
        failure.refresh_from_db()
        assert failure.state == CrosspostRetry.State.retrying
        assert calls == [(0, ["mastodon"])]

        # second retry while already retrying must not enqueue again
        client.post(reverse("users:crosspost_retry", args=[failure.pk]))
        assert calls == [(0, ["mastodon"])]

    def test_retry_owner_only(self, user, comment, client, monkeypatch):
        failure = CrosspostRetry.objects.create(
            user=user, piece=comment, platform="mastodon"
        )
        other = User.register(email="other@example.com", username="otheruser")
        client.force_login(other)
        response = client.post(reverse("users:crosspost_retry", args=[failure.pk]))
        assert response.status_code == 404
        failure.refresh_from_db()
        assert failure.state == CrosspostRetry.State.failed

    def test_retry_account_unlinked(self, user, comment, client, monkeypatch):
        failure = CrosspostRetry.objects.create(
            user=user, piece=comment, platform="bluesky"
        )
        calls = []
        monkeypatch.setattr(
            Piece,
            "sync_to_social_accounts",
            lambda self, update_mode=0, platforms=None: calls.append(platforms),
        )
        client.force_login(user)
        response = client.post(reverse("users:crosspost_retry", args=[failure.pk]))
        assert response.status_code == 200
        failure.refresh_from_db()
        assert failure.state == CrosspostRetry.State.failed
        assert calls == []

    def test_dismiss(self, user, comment, client):
        failure = CrosspostRetry.objects.create(
            user=user, piece=comment, platform="mastodon"
        )
        other = User.register(email="other2@example.com", username="otheruser2")
        client.force_login(other)
        client.post(reverse("users:crosspost_dismiss", args=[failure.pk]))
        assert CrosspostRetry.objects.filter(pk=failure.pk).exists()

        client.force_login(user)
        response = client.post(reverse("users:crosspost_dismiss", args=[failure.pk]))
        assert response.status_code == 200
        assert not CrosspostRetry.objects.filter(pk=failure.pk).exists()

    def test_status(self, user, comment, client):
        failure = CrosspostRetry.objects.create(
            user=user, piece=comment, platform="mastodon"
        )
        failure_id = failure.pk
        client.force_login(user)
        response = client.get(reverse("users:crosspost_status", args=[failure_id]))
        assert response.status_code == 200
        assert b"Test Book" in response.content

        failure.delete()
        response = client.get(reverse("users:crosspost_status", args=[failure_id]))
        assert response.status_code == 200
        assert b"Crossposted successfully" in response.content

    def test_status_stuck_retrying_times_out(self, user, comment, client):
        failure = CrosspostRetry.objects.create(
            user=user,
            piece=comment,
            platform="mastodon",
            state=CrosspostRetry.State.retrying,
        )
        CrosspostRetry.objects.filter(pk=failure.pk).update(
            edited_time=timezone.now() - timedelta(minutes=20)
        )
        client.force_login(user)
        response = client.get(reverse("users:crosspost_status", args=[failure.pk]))
        assert response.status_code == 200
        failure.refresh_from_db()
        assert failure.state == CrosspostRetry.State.failed

    def test_notification_status_flag(self, user, comment, client):
        client.force_login(user)
        url = reverse("social:unread_notifications_status")
        without_failure = client.get(url).content
        assert b"pending-dot" in without_failure
        assert b"hasCrosspostFailure = false" in without_failure
        # fresh user has no passkey, so the passkey nudge is pending
        assert b"needsPasskey = true" in without_failure

        CrosspostRetry.objects.create(user=user, piece=comment, platform="mastodon")
        with_failure = client.get(url).content
        assert b"hasCrosspostFailure = true" in with_failure

        session = client.session
        session["has_passkeys"] = True
        session.save()
        with_passkey = client.get(url).content
        assert b"needsPasskey = false" in with_passkey

    def test_pending_page_passkey_nudge(self, user, client):
        client.force_login(user)
        url = reverse("users:crossposts")
        response = client.get(url)
        assert b"passkey-nudge" in response.content

        session = client.session
        session["has_passkeys"] = True
        session.save()
        response = client.get(url)
        assert b"passkey-nudge" not in response.content


@pytest.mark.django_db(databases="__all__")
def test_prune_crosspost_retries(user, comment):
    old = CrosspostRetry.objects.create(
        user=user, piece=comment, platform="mastodon"
    )
    CrosspostRetry.objects.filter(pk=old.pk).update(
        created_time=timezone.now() - timedelta(days=30)
    )
    recent = CrosspostRetry.objects.create(
        user=user, piece=comment, platform="threads"
    )
    assert prune_crosspost_retries(days=28) == 1
    assert not CrosspostRetry.objects.filter(pk=old.pk).exists()
    assert CrosspostRetry.objects.filter(pk=recent.pk).exists()
