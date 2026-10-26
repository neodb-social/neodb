from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from users.models import User

STEAM = "https://steamcommunity.com/openid/login"


def _assertion(return_to: str) -> dict[str, str]:
    return {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "id_res",
        "openid.op_endpoint": STEAM,
        "openid.claimed_id": "https://steamcommunity.com/openid/id/76561197960435530",
        "openid.identity": "https://steamcommunity.com/openid/id/76561197960435530",
        "openid.return_to": return_to,
        "openid.response_nonce": "2026-10-25T00:00:00Zabc",
        "openid.assoc_handle": "1234567890",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
        "openid.sig": "c2ln",
    }


@pytest.mark.django_db(databases="__all__")
def test_callback_accepts_an_assertion_issued_for_this_site() -> None:
    user = User.register(email="steam@example.com", username="steamer")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    callback = reverse("users:steam_openid_callback")
    verified = mock.Mock(text="ns:http://specs.openid.net/auth/2.0\nis_valid:true\n")

    with mock.patch("users.views.steam.requests.post", return_value=verified) as post:
        response = client.get(callback, _assertion(f"http://testserver{callback}"))

    assert post.called
    assert response.status_code == 302
    assert client.session["steam_id"] == "76561197960435530"


@pytest.mark.django_db(databases="__all__")
@pytest.mark.parametrize(
    "tamper",
    [
        {"openid.return_to": "https://other.example/callback"},
        {"openid.op_endpoint": "https://evil.example/openid"},
        {"openid.signed": "signed,identity,response_nonce,assoc_handle"},
    ],
)
def test_callback_rejects_an_assertion_for_another_site(tamper: dict) -> None:
    user = User.register(email="steam2@example.com", username="steamer2")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    callback = reverse("users:steam_openid_callback")
    params = _assertion(f"http://testserver{callback}") | tamper

    with mock.patch("users.views.steam.requests.post") as post:
        response = client.get(callback, params)

    assert not post.called
    assert response.status_code == 302
    assert response["Location"] == reverse("users:import_steam")
    assert "steam_id" not in client.session
