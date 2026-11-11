import pytest

from api.models import Application, Authorization, Token


@pytest.mark.django_db
def test_bearer_token_cannot_authorize_new_grant(api_client, api_token, identity):
    """
    A bearer token is not a web session: it must not approve an OAuth grant,
    which could carry wider scopes than the token itself.
    """
    application = Application.objects.create(
        name="Escalation App",
        client_id="tk-escalate",
        client_secret="escalatesecret",
        redirect_uris="https://example.com/callback",
    )
    response = api_client.post(
        "/oauth/authorize",
        {
            "client_id": application.client_id,
            "redirect_uri": "https://example.com/callback",
            "scope": "read write push",
            "identity": identity.pk,
        },
    )
    assert response.status_code == 302
    assert "/auth/login" in response["Location"]
    assert not Authorization.objects.filter(application=application).exists()


@pytest.mark.django_db
def test_bearer_token_cannot_read_token_settings(api_client, api_token, identity):
    """
    The token settings pages reveal other applications' tokens, so a bearer
    token must not open them.
    """
    other = Token.objects.create(
        application=Application.objects.create(name="Other", client_id="tk-other"),
        user=identity.users.first(),
        identity=identity,
        token="othersecrettoken",
        scopes=["read", "write"],
    )
    for path in (
        f"/@{identity.handle}/settings/tokens/",
        f"/@{identity.handle}/settings/tokens/{other.pk}/",
    ):
        response = api_client.get(path)
        assert response.status_code != 200
        assert other.token.encode() not in response.content


@pytest.mark.django_db
def test_bearer_token_still_works_on_api(api_client, api_token):
    response = api_client.get("/api/v1/accounts/verify_credentials")
    assert response.status_code == 200


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


@pytest.mark.django_db
def test_token_non_ascii_credentials_rejected(client, identity):
    """
    A non-ASCII client_id/client_secret must fail authentication cleanly.
    hmac.compare_digest raises TypeError on non-ASCII str arguments, which
    turned a bad credential into a 500 instead of an access_denied.
    """
    application = Application.objects.create(
        name="Unicode App",
        client_id="tk-unicode-test",
        client_secret="unicodesecret",
        redirect_uris="https://example.com/callback",
    )
    Authorization.objects.create(
        application=application,
        user=identity.users.first(),
        identity=identity,
        code="unicodeauthcode",
        redirect_uri="https://example.com/callback",
        scopes=["read"],
    )

    for client_id, client_secret in [
        ("tk-unicode-tëst", "unicodesecret"),
        ("tk-unicode-test", "unicodesécret"),
        ("クライアント", "秘密"),
    ]:
        response = client.post(
            "/oauth/token",
            {
                "grant_type": "authorization_code",
                "code": "unicodeauthcode",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": "https://example.com/callback",
            },
        )
        assert response.status_code == 401
        assert response.json() == {"error": "access_denied"}

    # The rejected attempts must not have consumed the code
    response = client.post(
        "/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": "unicodeauthcode",
            "client_id": "tk-unicode-test",
            "client_secret": "unicodesecret",
            "redirect_uri": "https://example.com/callback",
        },
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
