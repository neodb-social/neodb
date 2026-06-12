import pytest
from django.urls import reverse

from mastodon.models import BlueskyAccount, MastodonAccount
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestLoginMethodSelection:
    def test_no_method(self, client):
        response = client.get(reverse("users:login"))
        assert response.status_code == 200
        assert response.context["selected_method"] == ""

    def test_bluesky_method(self, client):
        response = client.get(reverse("users:login"), {"method": "bluesky"})
        assert response.status_code == 200
        assert response.context["selected_method"] == "bluesky"
        assert b"var selected_method = 'bluesky'" in response.content

    def test_atproto_aliased_to_bluesky(self, client):
        # old notification messages persisted reauth URLs with ?method=atproto
        response = client.get(reverse("users:login"), {"method": "atproto"})
        assert response.status_code == 200
        assert response.context["selected_method"] == "bluesky"

    def test_unknown_method_ignored(self, client):
        response = client.get(reverse("users:login"), {"method": "x'</script>"})
        assert response.status_code == 200
        assert response.context["selected_method"] == ""


@pytest.mark.django_db(databases="__all__")
class TestReauthorizeUrl:
    def test_bluesky_points_to_bluesky_login_form(self):
        user = User.register(email="reauth@example.com", username="reauthuser")
        account = BlueskyAccount.objects.create(
            handle="reauth.bsky.social", user=user, domain="bsky.social", uid="1"
        )
        assert account.get_reauthorize_url() == reverse("users:login") + (
            "?method=bluesky"
        )

    def test_mastodon_points_to_oauth_flow(self):
        user = User.register(email="reauth2@example.com", username="reauthuser2")
        account = MastodonAccount.objects.create(
            handle="reauthuser2@mast.social",
            user=user,
            domain="mast.social",
            uid="2",
        )
        assert account.get_reauthorize_url() == reverse("mastodon:login") + (
            "?domain=mast.social"
        )
