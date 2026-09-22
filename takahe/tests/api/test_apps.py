import pytest
from django.test import Client

from api.models import Application, Token


@pytest.mark.django_db
def test_create(api_client):
    """
    Tests creating an app with mixed query/body params (some clients do this)
    """
    response = api_client.post(
        "/api/v1/apps?client_name=test",
        {"redirect_uris": "urn:ietf:wg:oauth:2.0:oob"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "test"
    assert response.json()["redirect_uris"] == ["urn:ietf:wg:oauth:2.0:oob"]


@pytest.mark.django_db
@pytest.mark.parametrize("redirect_uris", ["", " ", "\n", ",", [], [" "], ["", ","]])
def test_create_requires_a_redirect_uri(api_client, redirect_uris):
    """
    An app with no callback can never complete /oauth/authorize, so registering
    one fails here rather than at the authorization step. Applications already
    stored without a callback keep working; see Application.matches_redirect_uri.
    """
    response = api_client.post(
        "/api/v1/apps",
        {"client_name": "test", "redirect_uris": redirect_uris},
        content_type="application/json",
    )
    assert response.status_code == 422
    assert not Application.objects.filter(name="test").exists()


@pytest.mark.django_db
def test_create_joins_a_list_with_newlines(api_client):
    """
    A list registration is stored newline separated, so a URI holding a comma
    survives the round trip instead of being split in two.
    """
    uris = ["https://a.example/cb?ids=1,2", "https://b.example/cb"]
    response = api_client.post(
        "/api/v1/apps",
        {"client_name": "test", "redirect_uris": uris},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["redirect_uris"] == uris
    application = Application.objects.get(name="test")
    assert application.redirect_uris == "\n".join(uris)
    assert application.matches_redirect_uri("https://a.example/cb?ids=1,2")


@pytest.mark.django_db
def test_create_keeps_a_comma_inside_a_single_uri(api_client):
    """
    A lone registered URI holding a comma is still split for display by the
    legacy comma rule, but it must stay usable as a callback.
    """
    uri = "https://a.example/cb?ids=1,2"
    response = api_client.post("/api/v1/apps?client_name=test", {"redirect_uris": uri})
    assert response.status_code == 200
    application = Application.objects.get(name="test")
    assert application.redirect_uris == uri
    assert application.matches_redirect_uri(uri)


def _verify_credentials_client(
    identity, *, client_id: str, redirect_uris: str
) -> Client:
    """Build an authed client for an Application with the given redirect_uris."""
    application = Application.objects.create(
        name="Test App",
        client_id=client_id,
        client_secret="verifysecret",
        redirect_uris=redirect_uris,
    )
    token = Token.objects.create(
        application=application,
        user=identity.users.first(),
        identity=identity,
        token=f"token-{client_id}",
        scopes=["read"],
    )
    return Client(
        headers={
            "authorization": f"Bearer {token.token}",
            "accept": "application/json",
        }
    )


@pytest.mark.django_db
def test_verify_credentials_redirect_uri_shape(identity):
    """
    /api/v1/apps/verify_credentials must serialize redirect_uri as a string and
    redirect_uris as a list even though it is stored as a plain string,
    rather than raising a pydantic ValidationError.
    """
    client = _verify_credentials_client(
        identity, client_id="tk-verify-test", redirect_uris="neodb://oauth/callback"
    )

    response = client.get("/api/v1/apps/verify_credentials")
    assert response.status_code == 200
    data = response.json()
    assert data["redirect_uri"] == "neodb://oauth/callback"
    assert data["redirect_uris"] == ["neodb://oauth/callback"]
    # verify_credentials must not leak client keys
    assert data["client_secret"] == ""


@pytest.mark.django_db
def test_verify_credentials_splits_multiple_redirect_uris(identity):
    """
    Multiple redirect URIs stored as one delimited string (rows registered
    before add_app switched to newlines join with commas) must be split into
    separate entries, not returned as a single mashed-together element.
    """
    client = _verify_credentials_client(
        identity,
        client_id="tk-multi-test",
        redirect_uris="https://a.example/cb,https://b.example/cb",
    )

    response = client.get("/api/v1/apps/verify_credentials")
    assert response.status_code == 200
    data = response.json()
    assert data["redirect_uris"] == ["https://a.example/cb", "https://b.example/cb"]
    assert data["redirect_uri"] == "https://a.example/cb\nhttps://b.example/cb"
