import base64
import hashlib
import hmac
import json
import typing
from datetime import timedelta

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from common.models.site_config import SiteConfig
from journal.models.common import VisibilityType
from mastodon.models.threads import (
    THREADS_MAX_TEXT_LENGTH,
    Threads,
    ThreadsAccount,
    _truncate_for_threads,
)
from mastodon.views.threads import _parse_signed_request
from users.models import User

if typing.TYPE_CHECKING:
    from catalog.models import Item


class FakeItem:
    absolute_url = "https://example.org/movie/123"
    display_title = "Some Movie"


def _fake_item() -> "Item":
    return typing.cast("Item", FakeItem())


class FakeResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data
        self.content = json.dumps(data).encode()

    def json(self) -> dict:
        return self._data


def _request_with_session(path: str = "/account/threads/login"):
    request = RequestFactory().post(path)
    SessionMiddleware(lambda r: r).process_request(request)
    return request


def _signed_request(payload: dict, secret: str) -> str:
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{sig_b64}.{payload_b64}"


class TestTruncateForThreads:
    def test_short_text_unchanged(self):
        assert _truncate_for_threads("hello", _fake_item()) == "hello"

    def test_long_text_with_leading_link_kept(self):
        # comment-style: link near the beginning, user text after
        item = _fake_item()
        text = f"watched Some Movie\n{item.absolute_url}\n" + "x" * 1000
        result = _truncate_for_threads(text, item)
        assert len(result) <= THREADS_MAX_TEXT_LENGTH
        assert result.count(item.absolute_url) == 1
        assert result.endswith("……")

    def test_long_text_with_trailing_link_restored(self):
        # review-style: link at the end would be cut off, so re-append it
        item = _fake_item()
        text = "reviewed Some Movie\n" + "x" * 1000 + f"\n{item.absolute_url}"
        result = _truncate_for_threads(text, item)
        assert len(result) <= THREADS_MAX_TEXT_LENGTH
        assert result.endswith(item.absolute_url)

    def test_long_note_without_obj_restores_footer_link(self):
        # note-style: no obj passed, item link only exists in the footer
        text = "y" * 1000 + "\n—\n《Some Book》 p42\nhttps://example.org/book/456"
        result = _truncate_for_threads(text, None)
        assert len(result) <= THREADS_MAX_TEXT_LENGTH
        assert result.endswith("https://example.org/book/456")

    def test_long_text_without_any_link(self):
        result = _truncate_for_threads("z" * 1000, None)
        assert len(result) <= THREADS_MAX_TEXT_LENGTH
        assert result.endswith("……")

    def test_truncation_does_not_cut_link_midway(self):
        # place the link so it straddles the truncation point
        item = _fake_item()
        cut = THREADS_MAX_TEXT_LENGTH - len("……\n" + item.absolute_url)
        text = "x" * (cut - 10) + item.absolute_url + "y" * 200
        result = _truncate_for_threads(text, item)
        assert len(result) <= THREADS_MAX_TEXT_LENGTH
        assert result.endswith(item.absolute_url)
        # no broken partial URL left at the truncation point
        assert result.count("http") == 1


class TestGenerateAuthUrl:
    def test_auth_url_params_encoded(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "threads_app_id", "9999")
        request = _request_with_session()
        url = Threads.generate_auth_url(request)
        assert url.startswith("https://threads.net/oauth/authorize?")
        assert "client_id=9999" in url
        assert "threads_manage_replies" in url
        assert "redirect_uri=http%3A%2F%2Ftestserver%2Faccount%2Fthreads%2Foauth" in url
        assert "response_type=code" in url
        assert f"state={request.session['threads_oauth_state']}" in url


class TestPostSingle:
    def test_post_and_publish(self, monkeypatch):
        calls = []

        def fake_post(url, params=None, **kwargs):
            calls.append((url, dict(params or {})))
            return FakeResponse(200, {"id": f"id{len(calls)}"})

        monkeypatch.setattr("mastodon.models.threads.post", fake_post)
        assert Threads.post_single("tok", "42", "hello") == "id2"
        assert calls[0][0].endswith("/42/threads")
        assert calls[0][1]["media_type"] == "TEXT"
        assert calls[0][1]["text"] == "hello"
        assert "reply_to_id" not in calls[0][1]
        assert calls[1][0].endswith("/42/threads_publish")
        assert calls[1][1]["creation_id"] == "id1"

    def test_post_with_reply(self, monkeypatch):
        calls = []

        def fake_post(url, params=None, **kwargs):
            calls.append((url, dict(params or {})))
            return FakeResponse(200, {"id": f"id{len(calls)}"})

        monkeypatch.setattr("mastodon.models.threads.post", fake_post)
        assert Threads.post_single("tok", "42", "hello", reply_to_id="orig") == "id2"
        assert calls[0][1]["reply_to_id"] == "orig"

    def test_post_reply_fallback_without_permission(self, monkeypatch):
        # tokens authorized before threads_manage_replies was added to the
        # scope cannot create replies; fall back to a top-level post
        calls = []

        def fake_post(url, params=None, **kwargs):
            calls.append((url, dict(params or {})))
            if "reply_to_id" in (params or {}):
                return FakeResponse(400, {"error": {"message": "no permission"}})
            return FakeResponse(200, {"id": f"id{len(calls)}"})

        monkeypatch.setattr("mastodon.models.threads.post", fake_post)
        assert Threads.post_single("tok", "42", "hello", reply_to_id="orig") == "id3"
        assert "reply_to_id" in calls[0][1]
        assert "reply_to_id" not in calls[1][1]
        assert calls[2][0].endswith("/42/threads_publish")

    def test_post_network_error_returns_none(self, monkeypatch):
        def fake_post(url, params=None, **kwargs):
            raise OSError("connection failed")

        monkeypatch.setattr("mastodon.models.threads.post", fake_post)
        assert Threads.post_single("tok", "42", "hello") is None


class TestCheckAlive:
    def test_expired_token_not_alive(self):
        account = ThreadsAccount()
        account.access_token = "tok"
        account.token_expires_at = timezone.now() - timedelta(days=1)
        assert account.check_alive(save=False) is False

    def test_recently_reachable_skips_refresh(self):
        account = ThreadsAccount()
        account.access_token = "tok"
        account.token_expires_at = timezone.now() + timedelta(days=30)
        account.last_reachable = timezone.now() - timedelta(minutes=10)
        assert account.check_alive(save=False) is True

    def test_unrefreshable_but_unexpired_token_is_alive(self, monkeypatch):
        # long-lived tokens under 24 hours old cannot be refreshed yet,
        # this should not mark the account unreachable
        account = ThreadsAccount()
        account.access_token = "tok"
        account.token_expires_at = timezone.now() + timedelta(days=59)
        account.last_reachable = timezone.now() - timedelta(hours=2)
        monkeypatch.setattr(
            Threads, "refresh_token", staticmethod(lambda t: (None, None))
        )
        assert account.check_alive(save=False) is True
        assert account.access_token == "tok"

    def test_refresh_failure_without_expiry_not_alive(self, monkeypatch):
        account = ThreadsAccount()
        account.access_token = "tok"
        monkeypatch.setattr(
            Threads, "refresh_token", staticmethod(lambda t: (None, None))
        )
        assert account.check_alive(save=False) is False

    def test_refresh_success_updates_token(self, monkeypatch):
        account = ThreadsAccount()
        account.access_token = "tok"
        account.token_expires_at = timezone.now() + timedelta(days=30)
        account.last_reachable = timezone.now() - timedelta(days=2)
        monkeypatch.setattr(
            Threads, "refresh_token", staticmethod(lambda t: ("newtok", 5184000))
        )
        assert account.check_alive(save=False) is True
        assert account.access_token == "newtok"
        assert account.token_expires_at > timezone.now() + timedelta(days=59)


class TestAccountPost:
    def test_post_renders_and_truncates(self, monkeypatch):
        captured = {}

        def fake_post_single(token, user_id, text, reply_to_id=None):
            captured["text"] = text
            captured["reply_to_id"] = reply_to_id
            return "media1"

        monkeypatch.setattr(Threads, "post_single", staticmethod(fake_post_single))
        account = ThreadsAccount()
        account.access_token = "tok"
        account.uid = "42"
        item = _fake_item()
        content = "watched ##obj## ##rating##\n##obj_link_if_plain##" + "x" * 1000
        result = account.post(
            content, VisibilityType.Public, reply_to_id="orig", obj=item, rating=8
        )
        assert result == {"id": "media1"}
        assert captured["reply_to_id"] == "orig"
        assert len(captured["text"]) <= THREADS_MAX_TEXT_LENGTH
        assert item.display_title in captured["text"]
        assert item.absolute_url in captured["text"]


class TestSignedRequest:
    def test_roundtrip(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "threads_app_secret", "s3cret")
        payload = {"user_id": "123", "algorithm": "HMAC-SHA256"}
        assert _parse_signed_request(_signed_request(payload, "s3cret")) == payload

    def test_rejects_bad_signature(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "threads_app_secret", "s3cret")
        assert _parse_signed_request(_signed_request({"user_id": "123"}, "bad")) is None

    def test_rejects_garbage(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "threads_app_secret", "s3cret")
        assert _parse_signed_request("") is None
        assert _parse_signed_request("garbage") is None
        assert _parse_signed_request("a.b") is None

    def test_rejects_when_secret_unset(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "threads_app_secret", "")
        assert _parse_signed_request(_signed_request({"user_id": "123"}, "")) is None


@pytest.mark.django_db(databases="__all__")
class TestMetaCallbacks:
    # persist the secret in DB: SiteConfigMiddleware reloads SiteConfig.system
    # from DB during request processing, discarding in-memory monkeypatches

    def test_uninstall_clears_token(self, client):
        SiteConfig.set_system(threads_app_secret="s3cret")
        user = User.register(email="t@example.com", username="tuser")
        account = ThreadsAccount(
            user=user, domain=Threads.DOMAIN, uid="123", handle="tuser"
        )
        account.access_token = "tok"
        account.token_expires_at = timezone.now() + timedelta(days=30)
        account.save()
        sr = _signed_request({"user_id": "123"}, "s3cret")
        response = client.post(
            reverse("mastodon:threads_uninstall"), {"signed_request": sr}
        )
        assert response.status_code == 200
        account.refresh_from_db()
        # cleared token reads back as falsy (None or ""), check_alive treats both as missing
        assert not account.access_token
        assert account.token_expires_at is None

    def test_delete_removes_account_and_confirms(self, client):
        SiteConfig.set_system(threads_app_secret="s3cret")
        user = User.register(email="d@example.com", username="duser")
        account = ThreadsAccount(
            user=user, domain=Threads.DOMAIN, uid="456", handle="duser"
        )
        account.access_token = "tok"
        account.save()
        sr = _signed_request({"user_id": "456"}, "s3cret")
        response = client.post(
            reverse("mastodon:threads_delete"), {"signed_request": sr}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["confirmation_code"] == "threads_456"
        assert data["url"].startswith("http")
        assert not ThreadsAccount.objects.filter(
            uid="456", domain=Threads.DOMAIN
        ).exists()
        user.refresh_from_db()  # user itself is kept
        status = client.get(
            reverse("mastodon:threads_delete_status"),
            {"code": data["confirmation_code"]},
        )
        assert status.status_code == 200

    def test_callbacks_reject_invalid_payload(self, client):
        SiteConfig.set_system(threads_app_secret="s3cret")
        response = client.post(
            reverse("mastodon:threads_uninstall"), {"signed_request": "bad"}
        )
        assert response.status_code == 400
        response = client.post(reverse("mastodon:threads_delete"), {})
        assert response.status_code == 400

    def test_get_redirects_to_data_page(self, client):
        assert client.get(reverse("mastodon:threads_uninstall")).status_code == 302
        assert client.get(reverse("mastodon:threads_delete")).status_code == 302
