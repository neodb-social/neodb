import pytest
from django.test import Client

from api.models import Application, Token


@pytest.mark.django_db
def test_create(api_client):
    """
    Tests creating an app with mixed query/body params (some clients do this)
    """
    response = api_client.post("/api/v1/apps?client_name=test", {"redirect_uris": ""})
    assert response.status_code == 200
    assert response.json()["name"] == "test"


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
    rather than raising a pydantic ValidationError (NEODB-SOCIAL-7NS).
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
    Multiple redirect URIs stored as one delimited string (add_app joins a list
    with commas; Mastodon clients may use newlines) must be split into separate
    entries, not returned as a single mashed-together element.
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
