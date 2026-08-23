import socket
from importlib import import_module
from unittest.mock import patch

import django_rq
import httpx
import pytest
from django.test import override_settings
from django.urls import reverse

from catalog.models import Edition
from common.models import SiteConfig
from common.validators import _host_cache
from journal.models import Collection, Mark, Note, ShelfType
from users.models import User, Webhook
from users.models.webhook import (
    _bump_failures,
    _deliver_webhook,
    _FAIL_LIMIT,
    _post_webhook,
    clear_webhook_cache,
    clear_webhook_failures,
    dispatch_webhook,
    has_active_webhook,
    validate_webhook_url,
)


@pytest.fixture
def user():
    u = User.register(email="wh@example.com", username="whuser")
    clear_webhook_cache(u.pk)
    yield u
    clear_webhook_cache(u.pk)


@pytest.fixture
def webhook(user):
    w = Webhook.objects.create(user=user, url="https://hook.example.org/x")
    clear_webhook_cache(user.pk)
    clear_webhook_failures(w.pk)
    yield w
    clear_webhook_failures(w.pk)
    clear_webhook_cache(user.pk)


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

    def test_limit_zero_disables_dispatch(
        self,
        user,
        book,
        webhook,
        queue,
        monkeypatch,
        django_capture_on_commit_callbacks,
    ):
        monkeypatch.setattr(
            SiteConfig,
            "system",
            SiteConfig.system.model_copy(update={"webhook_max_subscriptions": 0}),
        )
        assert has_active_webhook(user.pk) is False
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

    def test_delivery_honors_lowered_limit(self, user, webhook, monkeypatch):
        called = []
        monkeypatch.setattr(
            "users.models.webhook._post_webhook",
            lambda url, payload, timeout: called.append(url) or True,
        )
        monkeypatch.setattr(
            SiteConfig,
            "system",
            SiteConfig.system.model_copy(update={"webhook_max_subscriptions": 0}),
        )
        _deliver_webhook(user.pk, {"type": "mark", "action": "save"})
        assert called == []


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
class TestPreferencesView:
    def _post_prefs(self, client, user, webhook_urls: str):
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        return client.post(reverse("users:preferences"), {"webhook_urls": webhook_urls})

    @pytest.fixture
    def data_views(self):
        # ``users.views.data`` the module is shadowed by the ``data`` view
        # function re-exported from the views package
        return import_module("users.views.data")

    def test_add_webhook(self, user, client, monkeypatch, data_views):
        monkeypatch.setattr(data_views, "validate_webhook_url", lambda url: True)
        self._post_prefs(client, user, "https://hook.example.org/a\n")
        urls = list(user.webhooks.values_list("url", flat=True))
        assert urls == ["https://hook.example.org/a"]

    def test_cap_and_invalid_dropped(self, user, client, monkeypatch, data_views):
        monkeypatch.setattr(
            data_views,
            "validate_webhook_url",
            lambda url: url.startswith("https://"),
        )
        assert SiteConfig.system.webhook_max_subscriptions == 1
        self._post_prefs(
            client,
            user,
            "not-a-url\nhttps://hook.example.org/a\nhttps://hook.example.org/b",
        )
        urls = list(user.webhooks.values_list("url", flat=True))
        assert urls == ["https://hook.example.org/a"]

    def test_validation_work_is_bounded(self, user, client, monkeypatch, data_views):
        checked = []

        def fake_validate(url):
            checked.append(url)
            return False

        monkeypatch.setattr(data_views, "validate_webhook_url", fake_validate)
        lines = "\n".join(f"https://h{i}.example.org/cb" for i in range(500))
        self._post_prefs(client, user, lines)
        max_n = SiteConfig.system.webhook_max_subscriptions
        assert len(checked) <= max_n + 10
        assert user.webhooks.count() == 0

    def test_remove_and_reenable(self, user, webhook, client, monkeypatch, data_views):
        monkeypatch.setattr(data_views, "validate_webhook_url", lambda url: True)
        webhook.disabled = True
        webhook.save(update_fields=["disabled"])
        _bump_failures(webhook.pk)
        self._post_prefs(client, user, webhook.url)
        webhook.refresh_from_db()
        assert webhook.disabled is False
        assert _bump_failures(webhook.pk) == 1  # counter was cleared
        self._post_prefs(client, user, "")
        assert user.webhooks.count() == 0
