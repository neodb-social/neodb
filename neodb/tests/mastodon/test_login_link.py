"""A logged-in visit to the Mastodon login link must not link a new identity."""

from urllib.parse import parse_qs, urlparse

import pytest
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from mastodon.models import Mastodon, MastodonAccount
from mastodon.models.mastodon import get_mastodon_login_url
from mastodon.views.mastodon import LINK_INTENT_SESSION_KEY
from users.models import User


class _App:
    client_id = "client"
    server_version = "Mastodon 4.3.0"


@pytest.fixture
def instance(monkeypatch: pytest.MonkeyPatch) -> dict[str, MastodonAccount | None]:
    """Stand in for a remote instance: any domain is accepted, and the
    callback yields whatever account ``returned["account"]`` holds."""
    returned: dict[str, MastodonAccount | None] = {"account": None}

    def generate_auth_url(domain: str, request) -> str:
        request.session["mastodon_domain"] = domain
        return get_mastodon_login_url(_App(), domain, request)

    monkeypatch.setattr(Mastodon, "generate_auth_url", staticmethod(generate_auth_url))
    monkeypatch.setattr(
        Mastodon, "obtain_token", staticmethod(lambda *a, **k: ("token", "refresh"))
    )
    monkeypatch.setattr(
        Mastodon, "authenticate", staticmethod(lambda *a, **k: returned["account"])
    )
    return returned


def _callback(client: Client, start_response: HttpResponse) -> HttpResponse:
    assert start_response.status_code == 302, start_response.content
    state = parse_qs(urlparse(start_response["Location"]).query)["state"][0]
    response = client.get(reverse("mastodon:oauth"), {"code": "c", "state": state})
    assert isinstance(response, HttpResponse)
    return response


def _new_account(domain: str = "evil.example") -> MastodonAccount:
    return MastodonAccount(handle=f"mallory@{domain}", domain=domain, uid="99")


@pytest.mark.django_db(databases="__all__")
class TestMastodonLoginLink:
    def test_get_cannot_start_a_flow_with_another_instance(self, client, instance):
        user = User.register(email="victim@example.com", username="victim")
        client.force_login(user)
        response = client.get(reverse("mastodon:login"), {"domain": "evil.example"})
        assert response.status_code != 302
        assert "mastodon_oauth_state" not in client.session
        assert not MastodonAccount.objects.filter(user=user).exists()

    def test_get_reauthorizes_the_linked_instance(self, client, instance):
        user = User.register(email="re@example.com", username="reuser")
        account = MastodonAccount.objects.create(
            handle="reuser@mast.example", user=user, domain="mast.example", uid="1"
        )
        client.force_login(user)
        response = client.get(account.get_reauthorize_url())
        assert response.status_code == 302
        assert response["Location"].startswith("https://mast.example/oauth/authorize")
        instance["account"] = account
        done = _callback(client, response)
        assert done.status_code == 302
        assert LINK_INTENT_SESSION_KEY not in client.session

    def test_reauthorization_cannot_swap_in_another_identity(self, client, instance):
        # the linked instance answers the callback with a different account
        user = User.register(email="swap@example.com", username="swapuser")
        account = MastodonAccount.objects.create(
            handle="swapuser@mast.example", user=user, domain="mast.example", uid="1"
        )
        client.force_login(user)
        response = client.get(account.get_reauthorize_url())
        instance["account"] = _new_account("mast.example")
        done = _callback(client, response)
        assert b"Security check failed" in done.content
        assert list(MastodonAccount.objects.filter(user=user)) == [account]

    def test_callback_without_link_intent_is_refused(self, client, instance):
        # e.g. a flow started while logged out and finished after logging in
        user = User.register(email="late@example.com", username="lateuser")
        client.force_login(user)
        session = client.session
        session["mastodon_oauth_state"] = "s"
        session["mastodon_domain"] = "evil.example"
        session.save()
        instance["account"] = _new_account()
        done = client.get(reverse("mastodon:oauth"), {"code": "c", "state": "s"})
        assert b"Security check failed" in done.content
        assert not MastodonAccount.objects.filter(user=user).exists()

    def test_intent_of_another_user_is_refused(self, client, instance):
        user = User.register(email="other@example.com", username="otheruser")
        client.force_login(user)
        session = client.session
        session["mastodon_oauth_state"] = "s"
        session["mastodon_domain"] = "evil.example"
        session[LINK_INTENT_SESSION_KEY] = {"user": user.pk + 1, "link": True}
        session.save()
        instance["account"] = _new_account()
        done = client.get(reverse("mastodon:oauth"), {"code": "c", "state": "s"})
        assert b"Security check failed" in done.content
        assert not MastodonAccount.objects.filter(user=user).exists()

    def test_post_from_account_settings_links_a_new_identity(self, client, instance):
        user = User.register(email="link@example.com", username="linkuser")
        client.force_login(user)
        response = client.post(
            reverse("mastodon:reconnect"), {"domain": "mast.example"}
        )
        instance["account"] = _new_account("mast.example")
        done = _callback(client, response)
        assert done.status_code == 302
        linked = MastodonAccount.objects.get(user=user)
        assert linked.handle == "mallory@mast.example"

    def test_logged_out_get_still_opens_the_login_form(self, client, instance):
        response = client.get(reverse("mastodon:login"), {"domain": "mast.example"})
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("users:login"))
