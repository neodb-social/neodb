import pytest
from django.core.exceptions import PermissionDenied, RequestAborted
from django.test import Client
from django.urls import reverse

from journal.models.collection import Collection
from mastodon.models.mastodon import MastodonAccount
from users.models import User


def _collection(user: User) -> Collection:
    return Collection.objects.create(
        owner=user.identity, title="shared list", visibility=0
    )


def _link_mastodon(user: User) -> MastodonAccount:
    return MastodonAccount.objects.create(
        handle="share@mast.social", user=user, domain="mast.social", uid="1"
    )


def _share(client: Client, collection: Collection, **extra):
    url = reverse("journal:collection_share", args=[collection.uuid])
    return client.post(url, {"comment": "note", "visibility": "2"}, **extra)


@pytest.mark.django_db(databases="__all__")
class TestCollectionShare:
    def setup_method(self):
        self.user = User.register(email="share@example.com", username="shareuser")
        self.collection = _collection(self.user)
        self.client = Client()
        self.client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")

    def test_without_mastodon_account_shows_error(self):
        response = _share(self.client, self.collection)
        assert response.status_code == 200
        assert "Link a Fediverse account" in response.content.decode()

    def test_without_mastodon_account_hides_share_link(self):
        response = self.client.get(self.collection.url)
        assert response.status_code == 200
        share_url = reverse("journal:collection_share", args=[self.collection.uuid])
        assert share_url not in response.content.decode()

    def test_with_mastodon_account_shows_share_link(self):
        _link_mastodon(self.user)
        response = self.client.get(self.collection.url)
        assert response.status_code == 200
        share_url = reverse("journal:collection_share", args=[self.collection.uuid])
        assert share_url in response.content.decode()

    def test_expired_token_redirects_to_relogin(self, monkeypatch):
        _link_mastodon(self.user)

        def denied(*args, **kwargs):
            raise PermissionDenied()

        monkeypatch.setattr(MastodonAccount, "post", denied)
        response = _share(self.client, self.collection)
        assert response.status_code == 200
        body = response.content.decode()
        assert "re-authenticate" in body
        assert reverse("mastodon:login") + "?domain=mast.social" in body

    def test_expired_token_htmx_redirects_to_relogin(self, monkeypatch):
        _link_mastodon(self.user)

        def denied(*args, **kwargs):
            raise PermissionDenied()

        monkeypatch.setattr(MastodonAccount, "post", denied)
        response = _share(self.client, self.collection, HTTP_HX_REQUEST="true")
        assert response.headers["HX-Redirect"].endswith("?domain=mast.social")

    def test_instance_failure_shows_error_without_relogin(self, monkeypatch):
        _link_mastodon(self.user)

        def aborted(*args, **kwargs):
            raise RequestAborted()

        monkeypatch.setattr(MastodonAccount, "post", aborted)
        response = _share(self.client, self.collection)
        assert response.status_code == 200
        body = response.content.decode()
        assert "Unable to crosspost" in body
        assert "re-authenticate" not in body

    def test_success_redirects(self, monkeypatch):
        _link_mastodon(self.user)
        posted = {}

        def ok(self, content, visibility, *args, **kwargs):
            posted["content"] = content
            return {"id": "1", "url": "https://mast.social/@share/1"}

        monkeypatch.setattr(MastodonAccount, "post", ok)
        response = _share(self.client, self.collection, HTTP_REFERER="/")
        assert response.status_code == 302
        assert "shared list" in posted["content"]
