import pytest
from django.templatetags.static import static


def _icon_url(identity_id) -> str:
    return f"/proxy/identity_icon/{identity_id}/"


def _expected_default_avatar() -> str:
    return static("img/avatar.png")


@pytest.mark.django_db
def test_missing_identity_redirects_to_default_avatar(client):
    """A non-existent identity id serves the default avatar instead of 404."""
    response = client.get(_icon_url(602895974513284306))
    assert response.status_code == 302
    assert response["Location"] == _expected_default_avatar()


@pytest.mark.django_db
def test_identity_without_icon_uri_redirects(client, remote_identity):
    """A remote identity with no stored icon_uri serves the default avatar."""
    assert not remote_identity.icon_uri
    response = client.get(_icon_url(remote_identity.pk))
    assert response.status_code == 302
    assert response["Location"] == _expected_default_avatar()


@pytest.mark.django_db
def test_local_identity_redirects(client, identity):
    """Local identities are not proxied; they serve the default avatar."""
    assert identity.local
    response = client.get(_icon_url(identity.pk))
    assert response.status_code == 302
    assert response["Location"] == _expected_default_avatar()


@pytest.mark.django_db
def test_remote_fetch_failure_redirects(client, remote_identity, httpx_mock):
    """A failing remote fetch falls back to the default avatar rather than 502."""
    remote_identity.icon_uri = "https://remote.test/missing-icon.png"
    remote_identity.save()
    httpx_mock.add_response(
        url="https://remote.test/missing-icon.png",
        status_code=404,
    )
    response = client.get(_icon_url(remote_identity.pk))
    assert response.status_code == 302
    assert response["Location"] == _expected_default_avatar()


@pytest.mark.django_db
def test_successful_proxy_returns_image(client, remote_identity, httpx_mock):
    """A reachable remote icon is proxied through unchanged."""
    remote_identity.icon_uri = "https://remote.test/icon.png"
    remote_identity.save()
    httpx_mock.add_response(
        url="https://remote.test/icon.png",
        status_code=200,
        content=b"fake-png-bytes",
        headers={"Content-Type": "image/png"},
    )
    response = client.get(_icon_url(remote_identity.pk))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content == b"fake-png-bytes"
