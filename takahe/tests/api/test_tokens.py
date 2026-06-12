import pytest

from api.models import Application, Authorization


@pytest.mark.django_db
def test_has_scope(api_token):
    """
    Tests has_scope on the Token model
    """
    assert api_token.has_scope("read")
    assert api_token.has_scope("read:statuses")
    assert not api_token.has_scope("destroyearth")


@pytest.mark.django_db
def test_authorization_code_single_use(client, identity):
    """
    An OAuth authorization code must mint exactly one token; replaying the
    same code must be rejected.
    """
    application = Application.objects.create(
        name="Code App",
        client_id="tk-code-test",
        client_secret="codesecret",
        redirect_uris="https://example.com/callback",
    )
    Authorization.objects.create(
        application=application,
        user=identity.users.first(),
        identity=identity,
        code="testauthcode",
        redirect_uri="https://example.com/callback",
        scopes=["read"],
    )
    data = {
        "grant_type": "authorization_code",
        "code": "testauthcode",
        "client_id": "tk-code-test",
        "client_secret": "codesecret",
        "redirect_uri": "https://example.com/callback",
    }

    response = client.post("/oauth/token", data)
    assert response.status_code == 200
    assert response.json()["access_token"]

    response = client.post("/oauth/token", data)
    assert response.status_code == 401
