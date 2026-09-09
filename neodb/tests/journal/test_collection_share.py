import pytest
from django.core.exceptions import PermissionDenied, RequestAborted
from django.test import Client
from django.urls import reverse

from journal.models.collection import Collection
from mastodon.models.bluesky import BlueskyAccount
from mastodon.models.mastodon import MastodonAccount
from takahe.models import Post
from takahe.utils import Takahe
from users.models import User


def _collection(user: User) -> Collection:
    c = Collection.objects.create(
        owner=user.identity, title="shared list", visibility=0
    )
    # refetch so latest_post is not a stale cached_property
    return Collection.objects.get(pk=c.pk)


def _latest_post(collection: Collection) -> Post:
    post = collection.latest_post
    assert post is not None
    return post


def _link_mastodon(user: User) -> MastodonAccount:
    return MastodonAccount.objects.create(
        handle="share@mast.social", user=user, domain="mast.social", uid="1"
    )


def _link_bluesky(user: User) -> BlueskyAccount:
    return BlueskyAccount.objects.create(
        handle="share.bsky.social", user=user, domain="bsky.social", uid="3"
    )


def _share(client: Client, collection: Collection, comment: str = "note", **extra):
    url = reverse("journal:collection_share", args=[collection.uuid])
    data = {"comment": comment, "visibility": extra.pop("visibility", "0")}
    return client.post(url, data, HTTP_REFERER="/", **extra)


@pytest.mark.django_db(databases="__all__")
class TestCollectionShareViaMastodon:
    def setup_method(self):
        self.user = User.register(email="share@example.com", username="shareuser")
        self.collection = _collection(self.user)
        self.client = Client()
        self.client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        _link_mastodon(self.user)

    def test_expired_token_redirects_to_relogin(self, monkeypatch):
        def denied(*args, **kwargs):
            raise PermissionDenied()

        monkeypatch.setattr(MastodonAccount, "post", denied)
        response = _share(self.client, self.collection)
        assert response.status_code == 200
        body = response.content.decode()
        assert "re-authenticate" in body
        assert reverse("mastodon:login") + "?domain=mast.social" in body

    def test_expired_token_htmx_redirects_to_relogin(self, monkeypatch):
        def denied(*args, **kwargs):
            raise PermissionDenied()

        monkeypatch.setattr(MastodonAccount, "post", denied)
        response = _share(self.client, self.collection, HTTP_HX_REQUEST="true")
        assert response.headers["HX-Redirect"].endswith("?domain=mast.social")

    def test_instance_failure_shows_error_without_relogin(self, monkeypatch):
        def aborted(*args, **kwargs):
            raise RequestAborted()

        monkeypatch.setattr(MastodonAccount, "post", aborted)
        response = _share(self.client, self.collection)
        assert response.status_code == 200
        body = response.content.decode()
        assert "Unable to crosspost" in body
        assert "re-authenticate" not in body

    def test_success_redirects(self, monkeypatch):
        posted = {}

        def ok(self, content, visibility, *args, **kwargs):
            posted["content"] = content
            return {"id": "1", "url": "https://mast.social/@share/1"}

        monkeypatch.setattr(MastodonAccount, "post", ok)
        response = _share(self.client, self.collection)
        assert response.status_code == 302
        assert "shared list" in posted["content"]
        assert "note" in posted["content"]


@pytest.mark.django_db(databases="__all__")
class TestCollectionShareViaBluesky:
    def setup_method(self):
        self.user = User.register(email="bsky@example.com", username="bskyuser")
        self.collection = _collection(self.user)
        self.client = Client()
        self.client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        _link_bluesky(self.user)

    def test_public_share_posts_to_bluesky(self, monkeypatch):
        posted = {}

        def ok(self, content, *args, **kwargs):
            posted["content"] = content
            posted["obj"] = kwargs.get("obj")
            return {"id": "at://x", "url": "https://bsky.app/x"}

        monkeypatch.setattr(BlueskyAccount, "post", ok)
        response = _share(self.client, self.collection)
        assert response.status_code == 302
        assert "##obj##" in posted["content"]
        assert "note" in posted["content"]
        assert posted["obj"].display_title == "shared list"
        assert posted["obj"].absolute_url == self.collection.absolute_url

    def test_failure_shows_error(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("pds down")

        monkeypatch.setattr(BlueskyAccount, "post", boom)
        response = _share(self.client, self.collection)
        assert response.status_code == 200
        assert "Unable to share to Bluesky" in response.content.decode()

    def test_non_public_share_stays_on_neodb(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("bluesky must not be used for non-public shares")

        monkeypatch.setattr(BlueskyAccount, "post", boom)
        response = _share(self.client, self.collection, visibility="1")
        assert response.status_code == 302
        quote = Post.objects.filter(
            author_id=self.user.identity.pk,
            quote_url=_latest_post(self.collection).object_uri,
        ).first()
        assert quote is not None
        assert quote.visibility == Takahe.Visibilities.followers


@pytest.mark.django_db(databases="__all__")
class TestCollectionShareLocally:
    def setup_method(self):
        self.user = User.register(email="local@example.com", username="localuser")
        self.owner = User.register(email="owner@example.com", username="listowner")
        self.collection = _collection(self.owner)
        self.client = Client()
        self.client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")

    def test_share_link_shown_without_any_social_account(self):
        response = self.client.get(self.collection.url)
        assert response.status_code == 200
        share_url = reverse("journal:collection_share", args=[self.collection.uuid])
        assert share_url in response.content.decode()

    def test_share_without_comment_boosts(self):
        post = _latest_post(self.collection)
        response = _share(self.client, self.collection, comment="")
        assert response.status_code == 302
        assert Takahe.post_boosted_by(post.pk, self.user.identity.pk)
        # a second share must not undo the boost
        _share(self.client, self.collection, comment="")
        assert Takahe.post_boosted_by(post.pk, self.user.identity.pk)

    def test_share_with_comment_quotes(self):
        post = _latest_post(self.collection)
        response = _share(self.client, self.collection, comment="great list")
        assert response.status_code == 302
        quote = Post.objects.filter(
            author_id=self.user.identity.pk, quote_url=post.object_uri
        ).first()
        assert quote is not None
        assert "great list" in quote.content
        assert "shared list" in quote.content
        assert "@listowner" in quote.content
        assert not Takahe.post_boosted_by(post.pk, self.user.identity.pk)

    def test_share_own_collection_with_comment(self):
        own = _collection(self.user)
        response = _share(self.client, own, comment="mine")
        assert response.status_code == 302
        quote = Post.objects.filter(
            author_id=self.user.identity.pk, quote_url=_latest_post(own).object_uri
        ).first()
        assert quote is not None
        assert "shared my collection" in quote.content
