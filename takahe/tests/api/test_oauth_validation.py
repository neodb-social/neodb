import base64

import pytest

from api.models import Application, Authorization


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    ("registered", "requested", "allowed"),
    [
        ("https://client.example/callback", "https://client.example/callback", True),
        ("https://client.example/callback", "https://client.example", False),
        ("https://evil.example.trusted.example/cb", "https://evil.example", False),
        ("https://client.example/callback", "callback", False),
        ("", "https://evil.example/cb", False),
        ("", "", False),
        ("https://a.example/cb,https://b.example/cb", "https://b.example/cb", True),
        (
            "https://a.example/cb\r\n https://b.example/cb ",
            "https://b.example/cb",
            True,
        ),
        ("neodb://oauth/callback", "neodb://oauth/callback", True),
        ("urn:ietf:wg:oauth:2.0:oob", "urn:ietf:wg:oauth:2.0:oob", True),
    ],
)
def test_authorization_requires_registered_redirect(
    client_with_user, identity, method, registered, requested, allowed
):
    application = Application.create("Redirect app", registered, None)
    response = getattr(client_with_user, method)(
        "/oauth/authorize",
        {
            "client_id": application.client_id,
            "redirect_uri": requested,
            "response_type": "code",
            "scope": "read",
            "identity": identity.pk,
        },
    )
    if allowed:
        assert response.status_code in (200, 302)
        assert Authorization.objects.count() == (1 if method == "post" else 0)
    else:
        assert 400 <= response.status_code < 500
        assert not Authorization.objects.exists()
        assert "Location" not in response


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", ["/oauth/token", "/oauth/revoke"])
@pytest.mark.parametrize(
    "header",
    [
        "Basic",
        "Basic !!!",
        "Basic abc",
        "Basic " + base64.b64encode(b"no-colon").decode(),
        "Basic " + base64.b64encode(b"\xff:secret").decode(),
        "Basic nonascii\N{LATIN SMALL LETTER E WITH ACUTE}",
        "Basic dXNlcjpwYXNz extra",
    ],
)
def test_malformed_basic_auth_is_rejected(client, endpoint, header):
    response = client.post(
        endpoint,
        {"grant_type": "authorization_code", "code": "unused", "token": "unused"},
        HTTP_AUTHORIZATION=header,
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


@pytest.mark.django_db
def test_revoke_missing_token_is_bad_request(client):
    response = client.post("/oauth/revoke", {})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_basic_auth_keeps_colons_in_secret(rf):
    from api.views.oauth import extract_client_info_from_basic_auth

    header = "Basic " + base64.b64encode(b"client:secret:with:colons").decode()
    request = rf.post("/oauth/token", HTTP_AUTHORIZATION=header)
    assert extract_client_info_from_basic_auth(request) == (
        "client",
        "secret:with:colons",
    )
