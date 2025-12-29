import json
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from catalog.models import Edition, Game, Movie
from journal.models import Collection, FeaturedCollection, Mark, Note, Review, Tag
from journal.models.shelf import ShelfType
from takahe.utils import Takahe
from users.models import User

CACHE_SETTINGS = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "calendar-api-tests",
    }
}
STORAGES_SETTINGS = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@pytest.mark.django_db(databases="__all__")
@override_settings(CACHES=CACHE_SETTINGS, STORAGES=STORAGES_SETTINGS)
def test_calendar_api_returns_calendar_data():
    cache.clear()
    user = User.register(email="cal@example.com", username="caluser")
    book = Edition.objects.create(title="Calendar Book")
    Mark(user.identity, book).update(ShelfType.COMPLETE, visibility=0)

    app = Takahe.get_or_create_app(
        "Calendar API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)

    client = Client()
    response = client.get(
        f"/api/user/{user.username}/calendar",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    date_key = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    assert date_key in payload
    assert "book" in payload[date_key]["items"]


@pytest.mark.django_db(databases="__all__")
@override_settings(CACHES=CACHE_SETTINGS, STORAGES=STORAGES_SETTINGS)
def test_calendar_api_follower_view():
    cache.clear()
    owner = User.register(email="owner@example.com", username="owneruser")
    follower = User.register(email="follower@example.com", username="followeruser")
    book = Edition.objects.create(title="Follower Book")
    movie = Movie.objects.create(title="Follower Movie")
    game = Game.objects.create(title="Follower Game")
    Mark(owner.identity, book).update(ShelfType.COMPLETE, visibility=0)
    Mark(owner.identity, movie).update(ShelfType.COMPLETE, visibility=1)
    Mark(owner.identity, game).update(ShelfType.COMPLETE, visibility=2)
    follower.identity.follow(owner.identity, True)
    app = Takahe.get_or_create_app(
        "Calendar API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=follower.identity.pk,
    )
    token = Takahe.refresh_token(app, follower.identity.pk, follower.pk)
    client = Client()
    response = client.get(
        f"/api/user/{owner.username}/calendar",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    date_key = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    assert date_key in payload
    items = payload[date_key]["items"]
    assert "book" in items
    assert "movie" in items
    assert "game" not in items


@pytest.mark.django_db(databases="__all__")
@override_settings(STORAGES=STORAGES_SETTINGS)
def test_collection_feature_toggle():
    owner = User.register(email="owner@example.com", username="owneruser")
    viewer = User.register(email="viewer@example.com", username="vieweruser")
    collection = Collection.objects.create(
        owner=owner.identity,
        title="Featured Collection",
        brief="",
        visibility=0,
    )
    book = Edition.objects.create(title="Featured Book")
    movie = Movie.objects.create(title="Featured Movie")
    collection.append_item(book)
    collection.append_item(movie)
    Mark(viewer.identity, book).update(ShelfType.WISHLIST, visibility=0)
    Mark(viewer.identity, movie).update(ShelfType.COMPLETE, visibility=0)

    app = Takahe.get_or_create_app(
        "Collection API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=viewer.identity.pk,
    )
    token = Takahe.refresh_token(app, viewer.identity.pk, viewer.pk)
    client = Client()

    response = client.post(
        f"/api/me/collection/featured/{collection.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert FeaturedCollection.objects.filter(
        owner=viewer.identity, target=collection
    ).exists()

    response = client.get(
        "/api/me/collection/featured/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["uuid"] == collection.uuid

    response = client.get(
        f"/api/me/collection/featured/{collection.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 302
    assert response["Location"] == f"/api/collection/{collection.uuid}"

    response = client.get(
        f"/api/me/collection/featured/{collection.uuid}/stats",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    stats = response.json()
    assert stats["total"] == 2
    assert stats["wishlist"] == 1
    assert stats["complete"] == 1
    assert stats["progress"] == 0
    assert stats["dropped"] == 0

    response = client.delete(
        f"/api/me/collection/featured/{collection.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert not FeaturedCollection.objects.filter(
        owner=viewer.identity, target=collection
    ).exists()


@pytest.mark.django_db(databases="__all__")
@override_settings(CACHES=CACHE_SETTINGS, STORAGES=STORAGES_SETTINGS)
def test_item_collections_visibility():
    with (
        patch("catalog.models.item.Item.update_index"),
        patch("journal.models.collection.Collection.sync_to_timeline"),
        patch("journal.models.collection.Collection.update_index"),
    ):
        owner = User.register(email="owner2@example.com", username="owneruser2")
        viewer = User.register(email="viewer2@example.com", username="vieweruser2")
        item = Edition.objects.create(title="Collections Item")
        public = Collection.objects.create(
            owner=owner.identity, title="Public Collection", brief="", visibility=0
        )
        follower_only = Collection.objects.create(
            owner=owner.identity,
            title="Follower Collection",
            brief="",
            visibility=1,
        )
        private = Collection.objects.create(
            owner=owner.identity, title="Private Collection", brief="", visibility=2
        )
        public.append_item(item)
        follower_only.append_item(item)
        private.append_item(item)

    viewer.identity.follow(owner.identity, True)
    app = Takahe.get_or_create_app(
        "Item Collection API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=viewer.identity.pk,
    )
    token = Takahe.refresh_token(app, viewer.identity.pk, viewer.pk)
    client = Client()

    response = client.get(
        f"/api/item/{item.uuid}/collection/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["pages"] == 1
    uuids = {c["uuid"] for c in payload["data"]}
    assert uuids == {public.uuid, follower_only.uuid}


@pytest.mark.django_db(databases="__all__")
@override_settings(STORAGES=STORAGES_SETTINGS)
def test_tag_api_lifecycle():
    user = User.register(email="tagger@example.com", username="tagger")
    item = Edition.objects.create(title="Tagged Book")

    app = Takahe.get_or_create_app(
        "Tag API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    response = client.post(
        "/api/me/tag/",
        data=json.dumps({"title": "Speculative", "visibility": 0}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    tag_payload = response.json()
    tag_uuid = tag_payload["uuid"]

    response = client.get(
        "/api/me/tag/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["uuid"] == tag_uuid

    response = client.get(
        f"/api/me/tag/{tag_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert response.json()["uuid"] == tag_uuid

    response = client.put(
        f"/api/me/tag/{tag_uuid}",
        data=json.dumps({"title": "Speculative", "visibility": 0}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.post(
        f"/api/me/tag/{tag_uuid}/item/",
        data=json.dumps({"item_uuid": item.uuid}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.get(
        f"/api/me/tag/{tag_uuid}/item/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["item"]["uuid"] == item.uuid

    response = client.delete(
        f"/api/me/tag/{tag_uuid}/item/{item.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.delete(
        f"/api/me/tag/{tag_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert Tag.get_by_url(tag_uuid) is None


@pytest.mark.django_db(databases="__all__")
@override_settings(STORAGES=STORAGES_SETTINGS)
def test_shelf_api_mark_and_lookup():
    user = User.register(email="shelf@example.com", username="shelfuser")
    marked = Edition.objects.create(title="Shelf Book")
    unmarked = Edition.objects.create(title="Unmarked Book")

    app = Takahe.get_or_create_app(
        "Shelf API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    response = client.post(
        f"/api/me/shelf/item/{marked.uuid}",
        data=json.dumps(
            {
                "shelf_type": "wishlist",
                "visibility": 0,
                "comment_text": "note",
                "rating_grade": 0,
                "tags": ["speculative"],
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.get(
        f"/api/me/shelf/item/{marked.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["shelf_type"] == "wishlist"
    assert payload["item"]["uuid"] == marked.uuid
    assert payload["comment_text"] == "note"

    response = client.get(
        f"/api/me/shelf/items/{marked.uuid},{unmarked.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["item"]["uuid"] == marked.uuid


@pytest.mark.django_db(databases="__all__")
@override_settings(STORAGES=STORAGES_SETTINGS)
def test_collection_api_crud_and_items():
    user = User.register(email="collector@example.com", username="collector")
    item = Edition.objects.create(title="Collection Book")

    app = Takahe.get_or_create_app(
        "Collection CRUD API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    response = client.post(
        "/api/me/collection/",
        data=json.dumps(
            {
                "title": "API Collection",
                "brief": "Short description",
                "visibility": 0,
                "query": None,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    collection = response.json()
    collection_uuid = collection["uuid"]

    response = client.get(
        "/api/me/collection/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["uuid"] == collection_uuid

    response = client.get(
        f"/api/me/collection/{collection_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.put(
        f"/api/me/collection/{collection_uuid}",
        data=json.dumps(
            {
                "title": "API Collection Updated",
                "brief": "Updated description",
                "visibility": 0,
                "query": None,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert response.json()["title"] == "API Collection Updated"

    response = client.post(
        f"/api/me/collection/{collection_uuid}/item/",
        data=json.dumps({"item_uuid": item.uuid, "note": "hello"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.get(
        f"/api/me/collection/{collection_uuid}/item/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["item"]["uuid"] == item.uuid
    assert payload["data"][0]["note"] == "hello"

    response = Client().get(f"/api/collection/{collection_uuid}")

    assert response.status_code == 200
    assert response.json()["uuid"] == collection_uuid

    response = Client().get(f"/api/collection/{collection_uuid}/item/")

    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.delete(
        f"/api/me/collection/{collection_uuid}/item/{item.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.get(
        f"/api/me/collection/{collection_uuid}/item/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert response.json()["count"] == 0

    response = client.delete(
        f"/api/me/collection/{collection_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    response = client.get(
        f"/api/me/collection/{collection_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
@override_settings(CACHES=CACHE_SETTINGS, STORAGES=STORAGES_SETTINGS)
def test_collection_trending_endpoint():
    cache.clear()
    owner = User.register(email="trend@example.com", username="trenduser")
    collection = Collection.objects.create(
        owner=owner.identity,
        title="Trending Collection",
        brief="",
        visibility=0,
    )
    cache.set("featured_collections", [collection.pk], timeout=None)

    response = Client().get("/api/trending/collection/")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["uuid"] == collection.uuid


@pytest.mark.django_db(databases="__all__")
@override_settings(STORAGES=STORAGES_SETTINGS)
def test_shelf_api_list_delete_and_logs():
    user = User.register(email="shelf-list@example.com", username="shelfuser2")
    item = Edition.objects.create(title="Shelf Log Book")
    Mark(user.identity, item).update(ShelfType.WISHLIST, visibility=0)

    app = Takahe.get_or_create_app(
        "Shelf API List Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    response = client.get(
        "/api/me/shelf/wishlist",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["item"]["uuid"] == item.uuid

    response = client.get(
        f"/api/user/{user.username}/shelf/wishlist",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1

    response = client.get(
        f"/api/me/shelf/item/{item.uuid}/logs",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1

    response = client.delete(
        f"/api/me/shelf/item/{item.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.get(
        f"/api/me/shelf/item/{item.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
@override_settings(STORAGES=STORAGES_SETTINGS)
def test_note_api_crud():
    user = User.register(email="note@example.com", username="noteuser")
    item = Edition.objects.create(title="Note Book")

    app = Takahe.get_or_create_app(
        "Note API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    response = client.post(
        f"/api/me/note/item/{item.uuid}/",
        data=json.dumps(
            {
                "title": "Note Title",
                "content": "Note Content",
                "sensitive": False,
                "progress_type": None,
                "progress_value": None,
                "visibility": 0,
                "post_to_fediverse": False,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    note_uuid = response.json()["uuid"]

    response = client.get(
        f"/api/me/note/item/{item.uuid}/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["uuid"] == note_uuid

    response = client.put(
        f"/api/me/note/{note_uuid}",
        data=json.dumps(
            {
                "title": "Updated Note",
                "content": "Updated Content",
                "sensitive": False,
                "progress_type": None,
                "progress_value": None,
                "visibility": 0,
                "post_to_fediverse": False,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Updated Note"

    response = client.delete(
        f"/api/me/note/{note_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.get(
        f"/api/me/note/item/{item.uuid}/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert Note.objects.filter(uid__isnull=False, item=item).count() == 0


@pytest.mark.django_db(databases="__all__")
@override_settings(STORAGES=STORAGES_SETTINGS)
def test_review_api_crud_and_public_fetch():
    user = User.register(email="review@example.com", username="reviewuser")
    item = Edition.objects.create(title="Review Book")

    app = Takahe.get_or_create_app(
        "Review API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    response = client.post(
        f"/api/me/review/item/{item.uuid}",
        data=json.dumps(
            {
                "title": "Review Title",
                "body": "Review Body",
                "visibility": 0,
                "created_time": None,
                "post_to_fediverse": False,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.get(
        "/api/me/review/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["item"]["uuid"] == item.uuid

    response = client.get(
        f"/api/me/review/item/{item.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    review = Review.objects.get(owner=user.identity, item=item)
    response = Client().get(f"/api/review/{review.uuid}")

    assert response.status_code == 200
    assert response.json()["url"].endswith(review.uuid)

    response = client.delete(
        f"/api/me/review/item/{item.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    response = client.get(
        f"/api/me/review/item/{item.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
@override_settings(STORAGES=STORAGES_SETTINGS)
def test_post_api_list_for_item():
    user = User.register(email="post@example.com", username="postuser")
    item = Edition.objects.create(title="Post Item")

    app = Takahe.get_or_create_app(
        "Post API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    class StubPosts(list):
        def prefetch_related(self, *args, **kwargs):
            return self

    class StubPost:
        def __init__(self, post_id):
            self.post_id = post_id

        def to_mastodon_json(self):
            return {
                "id": str(self.post_id),
                "uri": f"https://example.org/posts/{self.post_id}",
                "created_at": "2024-01-01T00:00:00Z",
                "account": {
                    "id": "1",
                    "username": "user",
                    "acct": "user",
                    "url": "https://example.org/@user",
                    "display_name": "User",
                    "note": "",
                    "avatar": "",
                    "avatar_static": "",
                    "header": "",
                    "header_static": "",
                    "locked": False,
                    "fields": [],
                    "emojis": [],
                    "bot": False,
                    "group": False,
                    "discoverable": True,
                    "indexable": True,
                    "moved": None,
                    "suspended": False,
                    "limited": False,
                    "created_at": "2024-01-01T00:00:00Z",
                },
                "content": "ok",
                "visibility": "public",
                "sensitive": False,
                "spoiler_text": "",
                "media_attachments": [],
                "mentions": [],
                "tags": [],
                "emojis": [],
                "reblogs_count": 0,
                "favourites_count": 0,
                "replies_count": 0,
                "url": f"https://example.org/posts/{self.post_id}",
                "in_reply_to_id": None,
                "in_reply_to_account_id": None,
                "language": None,
                "text": None,
                "edited_at": None,
                "favourited": False,
                "reblogged": False,
                "muted": False,
                "bookmarked": False,
                "pinned": False,
                "ext_neodb": None,
            }

    class StubResult:
        def __init__(self, posts):
            self.posts = posts
            self.pages = 1
            self.total = len(posts)

    class StubIndex:
        def __init__(self, result):
            self._result = result

        def search(self, query):
            return self._result

    stub_posts = StubPosts([StubPost(1), StubPost(2)])
    stub_result = StubResult(stub_posts)

    with patch(
        "journal.apis.post.JournalIndex.instance", return_value=StubIndex(stub_result)
    ):
        response = client.get(
            f"/api/item/{item.uuid}/posts/?type=comment",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["data"][0]["id"] == "1"
