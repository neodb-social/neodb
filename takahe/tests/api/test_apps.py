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


@pytest.mark.django_db
def test_verify_credentials_redirect_uri_shape(identity):
    """
    /api/v1/apps/verify_credentials must serialize redirect_uri as a string and
    redirect_uris as a list even though it is stored as a plain string,
    rather than raising a pydantic ValidationError (NEODB-SOCIAL-7NS).
    """
    application = Application.objects.create(
        name="Piecelet for NeoDB",
        client_id="tk-verify-test",
        client_secret="verifysecret",
        redirect_uris="neodb://oauth/callback",
    )
    token = Token.objects.create(
        application=application,
        user=identity.users.first(),
        identity=identity,
        token="verifytoken",
        scopes=["read"],
    )
    client = Client(
        headers={
            "authorization": f"Bearer {token.token}",
            "accept": "application/json",
        }
    )

    response = client.get("/api/v1/apps/verify_credentials")
    assert response.status_code == 200
    data = response.json()
    assert data["redirect_uri"] == "neodb://oauth/callback"
    assert data["redirect_uris"] == ["neodb://oauth/callback"]
    # verify_credentials must not leak client keys
    assert data["client_secret"] == ""
