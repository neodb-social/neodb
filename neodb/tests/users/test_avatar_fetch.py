import io
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from users.models import user as user_module
from users.models.user import _fetch_avatar

URL = "https://files.example.org/avatars/me.svg?x=1"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    return buf.getvalue()


class FakeStream:
    def __init__(self, status_code: int, content_type: str, body: bytes) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body

    def iter_bytes(self) -> Iterator[bytes]:
        for i in range(0, len(self._body), 1024):
            yield self._body[i : i + 1024]


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    monkeypatch.setattr(user_module, "is_valid_url", lambda url: True)
    return []


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    response: FakeStream,
) -> None:
    @contextmanager
    def fake_stream(method: str, url: str, **kwargs: Any) -> Iterator[FakeStream]:
        calls.append({"url": url, **kwargs})
        yield response

    monkeypatch.setattr(user_module.httpx, "stream", fake_stream)


def test_private_host_is_never_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(user_module, "is_valid_url", lambda url: False)
    _serve(monkeypatch, calls, FakeStream(200, "image/png", _png()))
    assert _fetch_avatar("https://10.0.0.1/a.png") is None
    assert calls == []


def test_png_is_kept_with_extension_from_format(monkeypatch, fetched) -> None:
    body = _png()
    _serve(monkeypatch, fetched, FakeStream(200, "image/png", body))
    assert _fetch_avatar(URL) == (body, "png")
    assert fetched[0]["follow_redirects"] is False


def test_redirect_is_refused(monkeypatch, fetched) -> None:
    _serve(monkeypatch, fetched, FakeStream(302, "text/html", b"<html></html>"))
    assert _fetch_avatar(URL) is None


def test_non_image_content_type_is_refused(monkeypatch, fetched) -> None:
    _serve(monkeypatch, fetched, FakeStream(200, "text/html", _png()))
    assert _fetch_avatar(URL) is None


def test_svg_is_refused(monkeypatch, fetched) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    _serve(monkeypatch, fetched, FakeStream(200, "image/svg+xml", svg))
    assert _fetch_avatar(URL) is None


def test_undecodable_image_is_refused(monkeypatch, fetched) -> None:
    _serve(monkeypatch, fetched, FakeStream(200, "image/png", b"not an image"))
    assert _fetch_avatar(URL) is None


def test_oversized_body_is_refused(monkeypatch, fetched) -> None:
    monkeypatch.setattr(user_module, "_AVATAR_MAX_BYTES", 100)
    _serve(monkeypatch, fetched, FakeStream(200, "image/png", _png() + b"\0" * 200))
    assert _fetch_avatar(URL) is None


@pytest.mark.django_db(databases="__all__")
def test_sync_identity_names_icon_by_detected_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = user_module.User.register(username="avatarsync")
    user.__dict__["mastodon"] = SimpleNamespace(
        display_name="Avatar Sync", note="", locked=False, avatar=URL
    )
    user.__dict__["threads"] = None
    user.__dict__["bluesky"] = None
    monkeypatch.setattr(user_module, "_fetch_avatar", lambda url: (_png(), "png"))
    user.sync_identity()
    icon = user.identity.takahe_identity.icon
    assert icon.name.endswith(".png")
    assert ".svg" not in icon.name
