import json
import socket
from unittest.mock import patch

import django_rq
import httpx
import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Edition
from common.validators import _host_cache
from journal.models import Collection, Mark, Note, ShelfType
from takahe.models import Token
from takahe.utils import Takahe
from users.models import User, Webhook
from users.models.webhook import (
    MAX_WEBHOOKS_PER_USER,
    _bump_failures,
    _deliver_webhook,
    _FAIL_LIMIT,
    _post_webhook,
    clear_webhook_cache,
    clear_webhook_failures,
    dispatch_webhook,
    has_active_webhook,
    remove_webhook,
    scope_set,
    set_webhook,
    validate_webhook_url,
)

_WEBHOOK_API = "/api/me/webhook"


@pytest.fixture
def user():
    u = User.register(email="wh@example.com", username="whuser")
    clear_webhook_cache(u.pk)
    yield u
    clear_webhook_cache(u.pk)


@pytest.fixture
def token(user):
    # a personal token: its own Application, held only by this user
    return Takahe.create_personal_token(user.identity.pk, user.pk, "hook app", "write")


@pytest.fixture
def webhook(user, token):
    w = set_webhook(user, token.application.pk, "https://hook.example.org/x")
    clear_webhook_failures(w.pk)
    yield w
    clear_webhook_failures(w.pk)
    clear_webhook_cache(user.pk)


def _api(client, method: str, token: Token, data: dict | None = None):
    kwargs = {"HTTP_AUTHORIZATION": f"Bearer {token.token}"}
    if data is not None:
        kwargs["data"] = json.dumps(data)
        kwargs["content_type"] = "application/json"
    return getattr(client, method)(_WEBHOOK_API, **kwargs)


@pytest.fixture
def book():
    return Edition.objects.create(title="Webhook Test Book")


class _FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue(self, func, *args, **kwargs):
        self.jobs.append((func, args))


@pytest.fixture
def queue(monkeypatch):
    # django_rq is a shared module: route only the webhook queue to the fake
    q = _FakeQueue()
    real_get_queue = django_rq.get_queue
    monkeypatch.setattr(
        "users.models.webhook.django_rq.get_queue",
        lambda name: q if name == "webhook" else real_get_queue(name),
    )
    return q


def _make_addr_info(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


class TestValidateWebhookUrl:
    def setup_method(self):
        _host_cache.clear()

    @override_settings(DEBUG=False)
    def test_https_public_host_accepted(self):
        with patch("socket.getaddrinfo", return_value=_make_addr_info("93.184.216.34")):
            assert validate_webhook_url("https://hooks.example.com/cb") is True

    @override_settings(DEBUG=False)
    def test_explicit_port_accepted(self):
        with patch("socket.getaddrinfo", return_value=_make_addr_info("93.184.216.34")):
            assert validate_webhook_url("https://hooks2.example.com:8443/cb") is True

    @override_settings(DEBUG=False)
    def test_http_rejected(self):
        assert validate_webhook_url("http://hooks.example.com/cb") is False

    @override_settings(DEBUG=False)
    def test_private_ip_rejected(self):
        with patch("socket.getaddrinfo", return_value=_make_addr_info("192.168.1.1")):
            assert validate_webhook_url("https://internal.example.com/cb") is False

    @override_settings(DEBUG=False)
    def test_garbage_rejected(self):
        assert validate_webhook_url("") is False
        assert validate_webhook_url("not a url") is False
        assert validate_webhook_url("https://" + "a" * 1000) is False

    @override_settings(DEBUG=True)
    def test_debug_allows_local_http(self):
        assert validate_webhook_url("http://localhost:8000/cb") is True
        assert validate_webhook_url("ftp://example.com/") is False


@pytest.mark.django_db(databases="__all__")
class TestDispatch:
    def test_no_webhook_no_enqueue(
        self, user, book, queue, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            Mark(user.identity, book).update(ShelfType.WISHLIST)
        assert queue.jobs == []

    def test_mark_update_enqueues_once(
        self, user, book, webhook, queue, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            Mark(user.identity, book).update(ShelfType.WISHLIST)
        assert len(queue.jobs) == 1
        func, args = queue.jobs[0]
        assert func is _deliver_webhook
        assert args[0] == user.pk
        payload = args[1]
        assert payload["type"] == "mark"
        assert payload["action"] == "save"
        assert payload["title"] == book.display_title
        assert payload["url"] == book.absolute_url

    def test_unmark_enqueues_delete(
        self, user, book, webhook, queue, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            Mark(user.identity, book).update(ShelfType.WISHLIST)
        queue.jobs.clear()
        with django_capture_on_commit_callbacks(execute=True):
            Mark(user.identity, book).delete()
        actions = [args[1]["action"] for _, args in queue.jobs]
        assert actions == ["delete"]

    def test_note_save_and_delete(
        self, user, book, webhook, queue, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            note = Note.objects.create(
                owner=user.identity, item=book, title="n", content="c", visibility=0
            )
        assert [args[1]["action"] for _, args in queue.jobs] == ["save"]
        assert queue.jobs[0][1][1]["type"] == "note"
        queue.jobs.clear()
        with django_capture_on_commit_callbacks(execute=True):
            note.delete()
        assert [args[1]["action"] for _, args in queue.jobs] == ["delete"]

    def test_collection_payload_uses_own_title_and_url(
        self, user, webhook, queue, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            collection = Collection.objects.create(
                owner=user.identity, title="my list", brief="b"
            )
        payload = queue.jobs[0][1][1]
        assert payload["type"] == "collection"
        assert payload["title"] == "my list"
        assert payload["url"] == collection.absolute_url

    def test_disabled_webhook_not_dispatched(
        self, user, book, webhook, queue, django_capture_on_commit_callbacks
    ):
        webhook.disabled = True
        webhook.save(update_fields=["disabled"])
        clear_webhook_cache(user.pk)
        with django_capture_on_commit_callbacks(execute=True):
            Mark(user.identity, book).update(ShelfType.WISHLIST)
        assert queue.jobs == []

    def test_dispatch_helper_gates_on_cache(
        self, user, webhook, queue, django_capture_on_commit_callbacks
    ):
        assert has_active_webhook(user.pk) is True
        with django_capture_on_commit_callbacks(execute=True):
            dispatch_webhook(user.pk, {"type": "note", "action": "save"})
        assert len(queue.jobs) == 1


@pytest.mark.django_db(databases="__all__")
class TestDeliver:
    def test_success_clears_counter(self, user, webhook, monkeypatch):
        sent = []

        def fake_post(url, payload, timeout):
            sent.append((url, payload))
            return True

        monkeypatch.setattr("users.models.webhook._post_webhook", fake_post)
        _bump_failures(webhook.pk)
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert sent == [(webhook.url, {"type": "mark", "action": "save"})]
        assert _bump_failures(webhook.pk) == 1  # was cleared by the success

    def test_failure_bumps_counter(self, user, webhook, monkeypatch):
        def fake_post(url, payload, timeout):
            raise OSError("connection refused")

        monkeypatch.setattr("users.models.webhook._post_webhook", fake_post)
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert _bump_failures(webhook.pk) == 2

    def test_unsuccessful_response_bumps_counter(self, user, webhook, monkeypatch):
        monkeypatch.setattr(
            "users.models.webhook._post_webhook", lambda url, payload, timeout: False
        )
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert _bump_failures(webhook.pk) == 2

    def test_disabled_after_limit(self, user, webhook, monkeypatch):
        monkeypatch.setattr(
            "users.models.webhook._post_webhook", lambda url, payload, timeout: False
        )
        for _ in range(_FAIL_LIMIT):
            _bump_failures(webhook.pk)
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        webhook.refresh_from_db()
        assert webhook.disabled is True
        assert has_active_webhook(user.pk) is False

    def test_replaced_webhook_not_disabled_by_stale_failure(
        self, user, token, webhook, monkeypatch
    ):
        def failing_post(url, payload, timeout):
            # the user replaces the URL while this delivery is in flight
            set_webhook(user, token.application.pk, "https://hook.example.org/new")
            return False

        monkeypatch.setattr("users.models.webhook._post_webhook", failing_post)
        for _ in range(_FAIL_LIMIT):
            _bump_failures(webhook.pk)
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        webhook.refresh_from_db()
        assert webhook.url == "https://hook.example.org/new"
        assert webhook.disabled is False

    def test_disabled_webhook_skipped(self, user, webhook, monkeypatch):
        webhook.disabled = True
        webhook.save(update_fields=["disabled"])
        called = []
        monkeypatch.setattr(
            "users.models.webhook._post_webhook",
            lambda url, payload, timeout: called.append(url) or True,
        )
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert called == []

    def test_revoked_application_dropped(self, user, token, webhook, monkeypatch):
        # token gone through any path (Mastodon API, logout everywhere...):
        # delivery removes the webhook instead of calling it
        token.delete()
        called = []
        monkeypatch.setattr(
            "users.models.webhook._post_webhook",
            lambda url, payload, timeout: called.append(url) or True,
        )
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert called == []
        assert not Webhook.objects.filter(pk=webhook.pk).exists()
        assert has_active_webhook(user.pk) is False

    def test_oauth_revoked_token_dropped(self, user, token, webhook, monkeypatch):
        # /oauth/revoke only stamps `revoked`, it does not delete the row
        token.revoked = timezone.now()
        token.save(update_fields=["revoked"])
        called = []
        monkeypatch.setattr(
            "users.models.webhook._post_webhook",
            lambda url, payload, timeout: called.append(url) or True,
        )
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert called == []
        assert not Webhook.objects.filter(pk=webhook.pk).exists()

    def test_surviving_write_only_token_not_enough(
        self, user, token, webhook, monkeypatch
    ):
        Token.objects.create(
            application=token.application,
            user_id=token.user_id,
            identity_id=token.identity_id,
            token="wo-" + token.token,
            scopes=["write"],
        )
        token.delete()
        called = []
        monkeypatch.setattr(
            "users.models.webhook._post_webhook",
            lambda url, payload, timeout: called.append(url) or True,
        )
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert called == []
        assert not Webhook.objects.filter(pk=webhook.pk).exists()

    def test_disabled_webhook_of_revoked_app_dropped(
        self, user, token, webhook, monkeypatch
    ):
        # a disabled row must not keep taking a slot once the app is gone
        webhook.disabled = True
        webhook.save(update_fields=["disabled"])
        token.delete()
        called = []
        monkeypatch.setattr(
            "users.models.webhook._post_webhook",
            lambda url, payload, timeout: called.append(url) or True,
        )
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert called == []
        assert not Webhook.objects.filter(pk=webhook.pk).exists()
        assert has_active_webhook(user.pk) is False

    def test_each_application_called_once(self, user, token, webhook, monkeypatch):
        other = Takahe.create_personal_token(user.identity.pk, user.pk, "b", "read")
        set_webhook(user, other.application.pk, "https://hook.example.org/b")
        called = []
        monkeypatch.setattr(
            "users.models.webhook._post_webhook",
            lambda url, payload, timeout: called.append(url) or True,
        )
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert called == ["https://hook.example.org/x", "https://hook.example.org/b"]


class TestPostWebhook:
    def _mock_client(self, monkeypatch, handler):
        real_client = httpx.Client

        def fake_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(**kwargs)

        monkeypatch.setattr("users.models.webhook.httpx.Client", fake_client)

    @override_settings(DEBUG=False)
    def test_post_pins_validated_ip(self, monkeypatch):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200)

        self._mock_client(monkeypatch, handler)
        with patch("socket.getaddrinfo", return_value=_make_addr_info("93.184.216.34")):
            ok = _post_webhook(
                "https://hooks.example.com:8443/cb?a=1", {"type": "ping"}, 1.0
            )
        assert ok is True
        request = seen[0]
        assert request.url.host == "93.184.216.34"
        assert request.url.port == 8443
        assert request.headers["host"] == "hooks.example.com:8443"
        assert request.extensions.get("sni_hostname") == "hooks.example.com"

    @override_settings(DEBUG=False)
    def test_post_refuses_private_resolution(self, monkeypatch):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200)

        self._mock_client(monkeypatch, handler)
        with patch("socket.getaddrinfo", return_value=_make_addr_info("10.0.0.8")):
            ok = _post_webhook("https://rebind.example.com/cb", {"type": "ping"}, 1.0)
        assert ok is False
        assert seen == []

    @override_settings(DEBUG=False)
    @pytest.mark.parametrize(
        "ip", ["100.64.0.1", "169.254.169.254", "127.0.0.1", "224.0.0.1", "fe80::1"]
    )
    def test_post_refuses_non_global_addresses(self, monkeypatch, ip):
        seen = []
        self._mock_client(monkeypatch, lambda request: seen.append(request))
        with patch("socket.getaddrinfo", return_value=_make_addr_info(ip)):
            ok = _post_webhook("https://shared.example.com/cb", {"type": "ping"}, 1.0)
        assert ok is False
        assert seen == []

    @override_settings(DEBUG=False)
    def test_post_reports_http_error(self, monkeypatch):
        self._mock_client(monkeypatch, lambda request: httpx.Response(500))
        with patch("socket.getaddrinfo", return_value=_make_addr_info("93.184.216.34")):
            ok = _post_webhook("https://hooks.example.com/cb", {"type": "ping"}, 1.0)
        assert ok is False

    @override_settings(DEBUG=True)
    def test_debug_posts_plain_url(self, monkeypatch):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200)

        self._mock_client(monkeypatch, handler)
        ok = _post_webhook("http://localhost:8000/cb", {"type": "ping"}, 1.0)
        assert ok is True
        assert seen[0].url.host == "localhost"


@pytest.mark.django_db(databases="__all__")
class TestWebhookApi:
    @pytest.fixture(autouse=True)
    def _accept_any_url(self, monkeypatch):
        monkeypatch.setattr(
            "users.apis.validate_webhook_url", lambda url: url.startswith("https://")
        )

    def test_get_without_webhook(self, client, token):
        assert _api(client, "get", token).status_code == 404

    def test_put_get_delete(self, user, client, token):
        r = _api(client, "put", token, {"url": " https://hook.example.org/api "})
        assert r.status_code == 200
        assert r.json() == {"url": "https://hook.example.org/api", "disabled": False}
        webhook = Webhook.objects.get(user=user, application_id=token.application_id)
        assert webhook.url == "https://hook.example.org/api"
        assert has_active_webhook(user.pk) is True

        r = _api(client, "get", token)
        assert r.status_code == 200
        assert r.json()["url"] == "https://hook.example.org/api"

        assert _api(client, "delete", token).status_code == 200
        assert not Webhook.objects.filter(pk=webhook.pk).exists()
        assert has_active_webhook(user.pk) is False

    def test_put_replaces_and_reenables(self, user, client, token, webhook):
        webhook.disabled = True
        webhook.save(update_fields=["disabled"])
        _bump_failures(webhook.pk)
        r = _api(client, "put", token, {"url": "https://hook.example.org/new"})
        assert r.status_code == 200
        webhook.refresh_from_db()
        assert webhook.url == "https://hook.example.org/new"
        assert webhook.disabled is False
        assert _bump_failures(webhook.pk) == 1  # counter was cleared
        assert user.webhooks.count() == 1

    def test_put_invalid_url(self, client, token):
        r = _api(client, "put", token, {"url": "http://hook.example.org/plain"})
        assert r.status_code == 400
        assert not Webhook.objects.exists()

    def test_scoped_to_token_application(self, user, client, token, webhook):
        other = Takahe.create_personal_token(user.identity.pk, user.pk, "b", "write")
        assert _api(client, "get", other).status_code == 404
        _api(client, "put", other, {"url": "https://hook.example.org/b"})
        assert user.webhooks.count() == 2
        assert _api(client, "delete", other).status_code == 200
        assert list(user.webhooks.values_list("url", flat=True)) == [webhook.url]

    def test_per_user_cap(self, user, client, token, webhook):
        tokens = [
            Takahe.create_personal_token(user.identity.pk, user.pk, f"t{i}", "read")
            for i in range(MAX_WEBHOOKS_PER_USER)
        ]
        for t in tokens:
            t.scopes = ["read", "write"]
            t.save(update_fields=["scopes"])
        # webhook fixture already holds one slot
        for t in tokens[:-1]:
            r = _api(client, "put", t, {"url": f"https://hook.example.org/{t.pk}"})
            assert r.status_code == 200
        r = _api(client, "put", tokens[-1], {"url": "https://hook.example.org/x"})
        assert r.status_code == 403
        assert user.webhooks.count() == MAX_WEBHOOKS_PER_USER
        # replacing an existing one is still allowed at the cap
        r = _api(client, "put", token, {"url": "https://hook.example.org/again"})
        assert r.status_code == 200
        assert user.webhooks.count() == MAX_WEBHOOKS_PER_USER
        # freeing a slot lets the rejected app in
        assert _api(client, "delete", tokens[0]).status_code == 200
        r = _api(client, "put", tokens[-1], {"url": "https://hook.example.org/x"})
        assert r.status_code == 200

    def test_replacement_allowed_over_cap(self, user, client, token, webhook):
        # rows beyond the cap can only predate it; replacing must still work
        for i in range(MAX_WEBHOOKS_PER_USER + 1):
            Webhook.objects.create(
                user=user, application_id=100000 + i, url=f"https://h.example/{i}"
            )
        r = _api(client, "put", token, {"url": "https://hook.example.org/again"})
        assert r.status_code == 200
        webhook.refresh_from_db()
        assert webhook.url == "https://hook.example.org/again"

    def test_cap_independent_per_user(self, user, client, token, webhook):
        other = User.register(email="wh3@example.com", username="whuser3")
        for i in range(MAX_WEBHOOKS_PER_USER):
            Webhook.objects.create(
                user=other, application_id=200000 + i, url=f"https://h.example/{i}"
            )
        t = Takahe.create_personal_token(user.identity.pk, user.pk, "mine", "write")
        r = _api(client, "put", t, {"url": "https://hook.example.org/mine"})
        assert r.status_code == 200

    def test_write_only_list_scopes_rejected(self, user, client):
        wo = Takahe.create_personal_token(user.identity.pk, user.pk, "wo", "write")
        wo.scopes = ["write", "push"]
        wo.save(update_fields=["scopes"])
        r = _api(client, "put", wo, {"url": "https://hook.example.org/wo"})
        assert r.status_code == 403
        # a scope merely containing the substring does not count either
        wo.scopes = "readonly write"
        wo.save(update_fields=["scopes"])
        r = _api(client, "put", wo, {"url": "https://hook.example.org/wo"})
        assert r.status_code == 403

    def test_requires_token(self, client):
        assert client.get(_WEBHOOK_API).status_code == 401
        r = client.put(
            _WEBHOOK_API,
            data='{"url": "https://x.y/"}',
            content_type="application/json",
        )
        assert r.status_code == 401
        assert client.delete(_WEBHOOK_API).status_code == 401

    def test_read_only_token_cannot_write(self, user, client):
        ro = Takahe.create_personal_token(user.identity.pk, user.pk, "ro", "read")
        r = _api(client, "put", ro, {"url": "https://hook.example.org/ro"})
        assert r.status_code == 401
        assert _api(client, "delete", ro).status_code == 401

    def test_write_only_token_cannot_subscribe(self, user, client):
        # payloads disclose what changed, so write alone is not enough
        wo = Takahe.create_personal_token(user.identity.pk, user.pk, "wo", "write")
        wo.scopes = "write"
        wo.save(update_fields=["scopes"])
        r = _api(client, "put", wo, {"url": "https://hook.example.org/wo"})
        assert r.status_code == 403
        assert not Webhook.objects.exists()

    def test_shared_application_isolated_per_user(self, user, client):
        other = User.register(email="wh2@example.com", username="whuser2")
        app = Takahe.get_or_create_app("shared", "", "", 0, client_id="app-shared-x")
        t1 = Takahe.get_token(Takahe.refresh_token(app, user.identity.pk, user.pk))
        t2 = Takahe.get_token(Takahe.refresh_token(app, other.identity.pk, other.pk))
        assert t1 and t2
        r = _api(client, "put", t1, {"url": "https://hook.example.org/u1"})
        assert r.status_code == 200
        assert _api(client, "get", t2).status_code == 404
        _api(client, "put", t2, {"url": "https://hook.example.org/u2"})
        assert Webhook.objects.get(user=user).url == "https://hook.example.org/u1"
        assert Webhook.objects.get(user=other).url == "https://hook.example.org/u2"
        assert _api(client, "delete", t2).status_code == 200
        assert Webhook.objects.filter(user=user).exists()
        clear_webhook_cache(other.pk)


@pytest.mark.django_db(databases="__all__")
class TestWebViews:
    @pytest.fixture
    def logged_in(self, client, user):
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        return client

    @pytest.fixture
    def dev_token(self, user):
        app = Takahe.get_or_create_app("", "", "", 0, client_id="app-00000000000-dev")
        return Takahe.refresh_token(app, user.identity.pk, user.pk)

    def test_console_requires_dev_token(self, user, logged_in, monkeypatch):
        monkeypatch.setattr("common.views.validate_webhook_url", lambda url: True)
        r = logged_in.post(
            reverse("common:developer_webhook"), {"url": "https://hook.example.org/c"}
        )
        assert r.status_code == 400
        assert user.webhooks.count() == 0
        html = logged_in.get(reverse("common:developer")).content.decode()
        assert "Generate a token first" in html

    def test_console_sets_and_clears_dev_webhook(
        self, user, logged_in, dev_token, monkeypatch
    ):
        monkeypatch.setattr("common.views.validate_webhook_url", lambda url: True)
        r = logged_in.post(
            reverse("common:developer_webhook"), {"url": "https://hook.example.org/c"}
        )
        assert r.status_code == 302
        webhook = user.webhooks.get()
        assert webhook.url == "https://hook.example.org/c"
        assert (
            Takahe.get_or_create_app("", "", "", 0, client_id="app-00000000000-dev").pk
            == webhook.application_id
        )

        r = logged_in.get(reverse("common:developer"))
        assert "https://hook.example.org/c" in r.content.decode()

        r = logged_in.post(reverse("common:developer_webhook"), {"url": ""})
        assert r.status_code == 302
        assert user.webhooks.count() == 0

    def test_console_enforces_cap(self, user, logged_in, dev_token, monkeypatch):
        monkeypatch.setattr("common.views.validate_webhook_url", lambda url: True)
        for i in range(MAX_WEBHOOKS_PER_USER):
            Webhook.objects.create(
                user=user, application_id=300000 + i, url=f"https://h.example/{i}"
            )
        r = logged_in.post(
            reverse("common:developer_webhook"), {"url": "https://hook.example.org/c"}
        )
        assert r.status_code == 400
        assert user.webhooks.count() == MAX_WEBHOOKS_PER_USER

    def test_console_rejects_invalid_url(self, user, logged_in, dev_token, monkeypatch):
        monkeypatch.setattr("common.views.validate_webhook_url", lambda url: False)
        r = logged_in.post(
            reverse("common:developer_webhook"), {"url": "https://bad.example/"}
        )
        assert r.status_code == 400
        assert user.webhooks.count() == 0

    def test_console_requires_login(self, client):
        r = client.post(reverse("common:developer_webhook"), {"url": "https://x.y/"})
        assert r.status_code == 302
        assert not Webhook.objects.exists()

    def test_account_page_shows_webhook_url(self, user, logged_in, token, webhook):
        other = Takahe.create_personal_token(user.identity.pk, user.pk, "plain", "read")
        html = logged_in.get(reverse("users:info")).content.decode()
        assert f'data-tooltip="{webhook.url}"' in html
        assert html.count("data-tooltip=") == 1
        assert other.application.name in html

    def test_account_page_escapes_url(self, user, logged_in, token):
        set_webhook(user, token.application.pk, 'https://h.example/"><b>x')
        html = logged_in.get(reverse("users:info")).content.decode()
        assert '"><b>x' not in html
        assert "&quot;&gt;&lt;b&gt;x" in html

    def test_account_page_marks_disabled(self, user, logged_in, webhook):
        webhook.disabled = True
        webhook.save(update_fields=["disabled"])
        html = logged_in.get(reverse("users:info")).content.decode()
        assert f'data-tooltip="{webhook.url}"' in html
        assert "disabled" in html

    def test_revoke_app_removes_webhook(self, user, logged_in, token, webhook):
        _bump_failures(webhook.pk)
        r = logged_in.post(
            reverse("users:authorized_app_revoke"), {"token_id": token.pk}
        )
        assert r.status_code == 302
        assert not Webhook.objects.filter(pk=webhook.pk).exists()
        assert has_active_webhook(user.pk) is False
        assert _bump_failures(webhook.pk) == 1  # counter was cleared

    def test_logout_everywhere_removes_webhooks(self, user, logged_in, webhook):
        r = logged_in.post(reverse("users:logout_everywhere"))
        assert r.status_code in (200, 302)
        assert user.webhooks.count() == 0

    def test_remove_webhook_all(self, user, token, webhook):
        other = Takahe.create_personal_token(user.identity.pk, user.pk, "b", "read")
        set_webhook(user, other.application.pk, "https://hook.example.org/b")
        remove_webhook(user.pk)
        assert user.webhooks.count() == 0


class TestScopeSet:
    def test_string_and_list_forms(self):
        assert scope_set("read write push") == {"read", "write", "push"}
        assert scope_set(["read", "write"]) == {"read", "write"}
        assert scope_set(None) == set()
        assert "read" not in scope_set("readonly write")
