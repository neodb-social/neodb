from types import SimpleNamespace

import httpx
import pytest
from pytest_httpx import HTTPXMock

from catalog.common.migrations import _make_migration_notifier
from common.models import SiteConfig
from common.utils import _discord_send, discord_webhook_post

WEBHOOK_ID = "123456789012345678"
TOKEN = "Ab3-_." * 11
CONFIGURED_URL = f"https://discordapp.com/api/webhooks/{WEBHOOK_ID}/{TOKEN}?x=1"
ENDPOINT = f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{TOKEN}"


def test_post_uses_canonical_endpoint_and_fields(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=ENDPOINT,
        method="POST",
        match_json={"content": "hi", "username": "@a", "thread_name": "t"},
    )
    assert (
        discord_webhook_post(CONFIGURED_URL, "hi", username="@a", thread_name="t")
        is None
    )


def test_post_rejects_non_discord_url() -> None:
    with pytest.raises(ValueError):
        discord_webhook_post(f"https://example.com/api/webhooks/1/{TOKEN}", "hi")


def _rate_limited(httpx_mock: HTTPXMock, retry_after: float, via: bool = True) -> None:
    httpx_mock.add_response(
        url=f"{ENDPOINT}?wait=true",
        method="POST",
        status_code=429,
        headers={"Retry-After": "1", **({"Via": "1.1 google"} if via else {})},
        json={"message": "You are being rate limited.", "retry_after": retry_after},
    )


@pytest.fixture
def delays(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("common.utils.time.sleep", slept.append)
    return slept


def test_post_waits_full_retry_after(httpx_mock: HTTPXMock, delays: list) -> None:
    _rate_limited(httpx_mock, 30.5)
    httpx_mock.add_response(
        url=f"{ENDPOINT}?wait=true", method="POST", json={"channel_id": "99"}
    )
    assert discord_webhook_post(CONFIGURED_URL, "hi", wait=True) == {"channel_id": "99"}
    assert delays == [30.5]


def test_post_gives_up_on_cloudflare_or_long_rate_limit(
    httpx_mock: HTTPXMock, delays: list
) -> None:
    _rate_limited(httpx_mock, 0.5, via=False)
    with pytest.raises(httpx.HTTPStatusError):
        discord_webhook_post(CONFIGURED_URL, "hi", wait=True)
    _rate_limited(httpx_mock, 600)
    with pytest.raises(httpx.HTTPStatusError):
        discord_webhook_post(CONFIGURED_URL, "hi", wait=True)
    assert delays == []


def test_post_retries_server_errors_with_backoff(
    httpx_mock: HTTPXMock, delays: list
) -> None:
    httpx_mock.add_response(url=ENDPOINT, method="POST", status_code=503)
    httpx_mock.add_response(url=ENDPOINT, method="POST", status_code=502)
    httpx_mock.add_response(url=ENDPOINT, method="POST")
    assert discord_webhook_post(CONFIGURED_URL, "hi") is None
    assert delays == [1, 3]


def test_send_logs_failure_without_raising(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    httpx_mock.add_response(
        url=ENDPOINT,
        method="POST",
        status_code=404,
        json={"message": "Unknown Webhook"},
    )
    _discord_send(CONFIGURED_URL, "hi")
    assert f"discord webhook {WEBHOOK_ID} send failed: HTTP 404" in caplog.text
    assert "Unknown Webhook" in caplog.text
    assert TOKEN not in caplog.text


def test_migration_notifier_posts_into_one_thread(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        SiteConfig,
        "system",
        SimpleNamespace(discord_webhooks={"default": CONFIGURED_URL}),
        raising=False,
    )
    httpx_mock.add_response(
        url=f"{ENDPOINT}?wait=true",
        method="POST",
        match_json={"content": "start", "thread_name": "fix_x"},
        json={"id": "1", "channel_id": "777"},
    )
    httpx_mock.add_response(
        url=f"{ENDPOINT}?thread_id=777",
        method="POST",
        match_json={"content": "done"},
    )
    notify = _make_migration_notifier("fix_x")
    notify("start")
    notify("done")
