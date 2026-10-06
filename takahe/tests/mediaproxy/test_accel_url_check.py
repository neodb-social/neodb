import socket

import pytest

from mediaproxy.views import is_public_http_url

ACCEL = {"HTTP_X_TAKAHE_ACCEL": "1"}


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/admin/",
        "https://10.0.0.1/a.png",
        "https://[::1]/a.png",
        "file:///etc/passwd",
        "gopher://1.1.1.1/x",
        "http:///nohost",
        "https://xn--ls8h.la/a.png",
    ],
)
def test_is_public_http_url_refuses(url):
    assert not is_public_http_url(url)


def test_is_public_http_url_accepts_public_address():
    assert is_public_http_url("https://1.1.1.1/icon.png")


def test_is_public_http_url_memoizes_verdict_per_host(settings, monkeypatch):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    lookups: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def counting_getaddrinfo(host, *args, **kwargs):
        lookups.append(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", counting_getaddrinfo)
    assert is_public_http_url("https://1.1.1.1/a.png")
    assert is_public_http_url("https://1.1.1.1/b.png")
    assert not is_public_http_url("https://10.0.0.1/a.png")
    assert not is_public_http_url("https://10.0.0.1/b.png")
    assert lookups == ["1.1.1.1", "10.0.0.1"]


def test_is_public_http_url_does_not_cache_resolution_failure(settings, monkeypatch):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    calls = 0

    def failing_getaddrinfo(host, *args, **kwargs):
        nonlocal calls
        calls += 1
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)
    assert not is_public_http_url("https://nx.example/a.png")
    assert not is_public_http_url("https://nx.example/a.png")
    assert calls == 2


@pytest.mark.django_db
def test_accel_refuses_internal_image_url(client, remote_identity):
    remote_identity.image_uri = "http://169.254.169.254/latest/meta-data/"
    remote_identity.save()
    response = client.get(f"/proxy/identity_image/{remote_identity.pk}/", **ACCEL)
    assert response.status_code == 404
    assert "X-Takahe-RealUri" not in response.headers


@pytest.mark.django_db
def test_accel_icon_falls_back_to_default_avatar(client, remote_identity):
    remote_identity.icon_uri = "http://10.0.0.1/icon.png"
    remote_identity.save()
    response = client.get(f"/proxy/identity_icon/{remote_identity.pk}/", **ACCEL)
    assert response.status_code == 302
    assert "X-Takahe-RealUri" not in response.headers


@pytest.mark.django_db
def test_non_http_scheme_refused_without_accel(client, remote_identity):
    remote_identity.image_uri = "file:///etc/passwd"
    remote_identity.save()
    response = client.get(f"/proxy/identity_image/{remote_identity.pk}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_accel_hands_public_url_to_nginx(client, remote_identity):
    remote_identity.image_uri = "https://1.1.1.1/header.png"
    remote_identity.save()
    response = client.get(f"/proxy/identity_image/{remote_identity.pk}/", **ACCEL)
    assert response.status_code == 200
    assert response.headers["X-Takahe-RealUri"] == "https://1.1.1.1/header.png"
