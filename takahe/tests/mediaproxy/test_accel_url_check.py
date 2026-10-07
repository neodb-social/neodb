import socket

import pytest
from django.core.cache import cache

from mediaproxy import views
from mediaproxy.views import public_http_url_verdict

ACCEL = {"HTTP_X_TAKAHE_ACCEL": "1"}


def make_unresolvable(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    # patch only after fixtures have saved rows: the job queue resolves redis
    lookups: list[str] = []

    def failing_getaddrinfo(host, *args, **kwargs):
        lookups.append(host)
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)
    return lookups


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
    ],
)
def test_verdict_refuses(url):
    assert public_http_url_verdict(url) is False


def test_verdict_never_accepts_invalid_idna_host():
    assert public_http_url_verdict("https://xn--ls8h.la/a.png") is not True


def test_verdict_accepts_public_address():
    assert public_http_url_verdict("https://1.1.1.1/icon.png") is True


def test_verdict_memoized_per_host(monkeypatch):
    lookups: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def counting_getaddrinfo(host, *args, **kwargs):
        lookups.append(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", counting_getaddrinfo)
    assert public_http_url_verdict("https://1.1.1.1/a.png") is True
    assert public_http_url_verdict("https://1.1.1.1/b.png") is True
    assert public_http_url_verdict("https://10.0.0.1/a.png") is False
    assert public_http_url_verdict("https://10.0.0.1/b.png") is False
    assert lookups == ["1.1.1.1", "10.0.0.1"]


def test_verdict_unresolved_host_is_none_and_memoized_briefly(monkeypatch):
    unresolvable = make_unresolvable(monkeypatch)
    assert public_http_url_verdict("https://nx.example/a.png") is None
    assert public_http_url_verdict("https://nx.example/b.png") is None
    assert unresolvable == ["nx.example"]


def test_verdict_ttls(monkeypatch):
    ttls: dict[str, tuple[object, int]] = {}
    real_set = cache.set

    def recording_set(key, value, timeout, *args, **kwargs):
        ttls[key] = (value, timeout)
        return real_set(key, value, timeout, *args, **kwargs)

    monkeypatch.setattr(views.cache, "set", recording_set)
    public_http_url_verdict("https://1.1.1.1/a.png")
    public_http_url_verdict("https://10.0.0.1/a.png")

    make_unresolvable(monkeypatch)
    public_http_url_verdict("https://nx.example/a.png")
    assert ttls == {
        "mediaproxy:host_ok:1.1.1.1:443": (True, 300),
        "mediaproxy:host_ok:10.0.0.1:443": (False, 300),
        "mediaproxy:host_ok:nx.example:443": ("nores", 60),
    }


@pytest.mark.django_db
def test_accel_refuses_internal_image_url(client, remote_identity):
    remote_identity.image_uri = "http://169.254.169.254/latest/meta-data/"
    remote_identity.save()
    response = client.get(f"/proxy/identity_image/{remote_identity.pk}/", **ACCEL)
    assert response.status_code == 404
    assert "X-Takahe-RealUri" not in response.headers


@pytest.mark.django_db
def test_accel_unresolvable_image_host_is_uncached_503(
    client, remote_identity, monkeypatch
):
    remote_identity.image_uri = "https://nx.example/header.png"
    remote_identity.save()
    make_unresolvable(monkeypatch)
    response = client.get(f"/proxy/identity_image/{remote_identity.pk}/", **ACCEL)
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Accel-Expires"] == "0"
    assert "X-Takahe-RealUri" not in response.headers


@pytest.mark.django_db
def test_accel_icon_falls_back_to_default_avatar(client, remote_identity):
    remote_identity.icon_uri = "http://10.0.0.1/icon.png"
    remote_identity.save()
    response = client.get(f"/proxy/identity_icon/{remote_identity.pk}/", **ACCEL)
    assert response.status_code == 302
    assert response.headers["Cache-Control"] == "public, max-age=3600"
    assert "X-Takahe-RealUri" not in response.headers


@pytest.mark.django_db
def test_accel_unresolvable_icon_host_falls_back_uncached(
    client, remote_identity, monkeypatch
):
    remote_identity.icon_uri = "https://nx.example/icon.png"
    remote_identity.save()
    make_unresolvable(monkeypatch)
    response = client.get(f"/proxy/identity_icon/{remote_identity.pk}/", **ACCEL)
    assert response.status_code == 302
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Accel-Expires"] == "0"
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
