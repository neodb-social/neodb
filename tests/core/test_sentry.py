from types import SimpleNamespace

import sentry_sdk

from common import sentry
from takahe import ap_handlers


def test_url_domain_extracts_hostname():
    assert sentry.url_domain("https://Example.com/path") == "example.com"
    assert sentry.url_domain("mastodon.social") == "mastodon.social"
    assert sentry.url_domain("") == "unknown"


def test_count_noops_when_sentry_is_not_initialized(monkeypatch):
    calls = []
    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: False)
    monkeypatch.setattr(
        sentry_sdk.metrics,
        "count",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    sentry.count("neodb.test", attributes={"domain": "example.com"})

    assert calls == []


def test_count_emits_when_sentry_is_initialized(monkeypatch):
    calls = []
    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: True)
    monkeypatch.setattr(
        sentry_sdk.metrics,
        "count",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    sentry.count(
        "neodb.test",
        2,
        attributes={"domain": "example.com", "empty": None},
    )

    assert calls == [(("neodb.test", 2), {"attributes": {"domain": "example.com"}})]


def test_remote_post_domain_uses_author_uri_domain():
    post = SimpleNamespace(
        author=SimpleNamespace(
            uri_domain="remote.example",
            actor_uri="https://fallback.example/@user",
        )
    )

    assert ap_handlers._remote_post_domain(post) == "remote.example"


def test_record_remote_post_fetched_logs_domain(monkeypatch):
    calls = []
    post = SimpleNamespace(
        author=SimpleNamespace(
            uri_domain=None,
            actor_uri="https://remote.example/@user",
        )
    )
    monkeypatch.setattr(
        ap_handlers,
        "sentry_count",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    ap_handlers._record_remote_post_fetched(post)

    assert calls == [
        (
            ("post.fetched",),
            {"attributes": {"domain": "remote.example"}},
        )
    ]
