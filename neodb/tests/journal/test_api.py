import json
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from catalog.models import Edition, Game, Movie
from journal.models import (
    Article,
    Collection,
    FeaturedCollection,
    Mark,
    Note,
    Review,
    Tag,
)
from journal.models.shelf import ShelfType
from takahe.models import Post
from takahe.utils import Takahe
from users.models import User

CACHE_SETTINGS = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "calendar-api-tests",
    }
}


@pytest.mark.django_db(databases="__all__")
@override_settings(CACHES=CACHE_SETTINGS)
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
@override_settings(CACHES=CACHE_SETTINGS)
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
    detail = response.json()
    assert detail["uuid"] == collection_uuid
    assert detail["item_count_by_category"]["book"] == 1
    assert detail["item_count_by_category"]["movie"] == 0

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
def test_collection_reorder_items():
    user = User.register(email="reorder@example.com", username="reorderer")
    book1 = Edition.objects.create(title="Reorder Book 1")
    book2 = Edition.objects.create(title="Reorder Book 2")
    book3 = Edition.objects.create(title="Reorder Book 3")
    collection = Collection.objects.create(
        owner=user.identity,
        title="Reorder Collection",
        brief="",
        visibility=0,
    )
    collection.append_item(book1)
    collection.append_item(book2)
    collection.append_item(book3)

    app = Takahe.get_or_create_app(
        "Collection Reorder API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    response = client.post(
        f"/api/me/collection/{collection.uuid}/reorder_items",
        data=json.dumps({"item_uuids": [book3.uuid, book1.uuid, book2.uuid]}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200

    ordered = [m.item.uuid for m in collection.ordered_members]
    assert ordered == [book3.uuid, book1.uuid, book2.uuid]


@pytest.mark.django_db(databases="__all__")
def test_collection_reorder_items_validation():
    user = User.register(email="reorder2@example.com", username="reorderer2")
    book1 = Edition.objects.create(title="Reorder2 Book 1")
    book2 = Edition.objects.create(title="Reorder2 Book 2")
    extra = Edition.objects.create(title="Reorder2 Book Extra")
    collection = Collection.objects.create(
        owner=user.identity,
        title="Reorder2 Collection",
        brief="",
        visibility=0,
    )
    collection.append_item(book1)
    collection.append_item(book2)

    app = Takahe.get_or_create_app(
        "Collection Reorder Validation Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    # Duplicate uuids in payload -> 400
    response = client.post(
        f"/api/me/collection/{collection.uuid}/reorder_items",
        data=json.dumps({"item_uuids": [book1.uuid, book1.uuid]}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 400

    # Partial list missing book2 -> 400
    response = client.post(
        f"/api/me/collection/{collection.uuid}/reorder_items",
        data=json.dumps({"item_uuids": [book1.uuid]}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 400

    # Unknown uuid in payload -> 400
    response = client.post(
        f"/api/me/collection/{collection.uuid}/reorder_items",
        data=json.dumps({"item_uuids": [book1.uuid, extra.uuid]}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 400

    # Order remains untouched after rejected requests
    ordered = [m.item.uuid for m in collection.ordered_members]
    assert ordered == [book1.uuid, book2.uuid]


@pytest.mark.django_db(databases="__all__")
def test_collection_reorder_items_permissions():
    owner = User.register(email="reorder_owner@example.com", username="reorderowner")
    intruder = User.register(
        email="reorder_intruder@example.com", username="reorderintruder"
    )
    book1 = Edition.objects.create(title="Reorder3 Book 1")
    book2 = Edition.objects.create(title="Reorder3 Book 2")
    collection = Collection.objects.create(
        owner=owner.identity,
        title="Reorder3 Collection",
        brief="",
        visibility=0,
    )
    collection.append_item(book1)
    collection.append_item(book2)

    app = Takahe.get_or_create_app(
        "Collection Reorder Permission Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=intruder.identity.pk,
    )
    intruder_token = Takahe.refresh_token(app, intruder.identity.pk, intruder.pk)
    client = Client()

    # Non-owner -> 403
    response = client.post(
        f"/api/me/collection/{collection.uuid}/reorder_items",
        data=json.dumps({"item_uuids": [book2.uuid, book1.uuid]}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {intruder_token}",
    )
    assert response.status_code == 403

    # Missing collection -> 404
    response = client.post(
        "/api/me/collection/does-not-exist/reorder_items",
        data=json.dumps({"item_uuids": []}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {intruder_token}",
    )
    assert response.status_code == 404

    # Dynamic collection -> 403
    dynamic = Collection.objects.create(
        owner=intruder.identity,
        title="Dynamic Reorder",
        brief="",
        visibility=0,
        query="tag:any",
    )
    response = client.post(
        f"/api/me/collection/{dynamic.uuid}/reorder_items",
        data=json.dumps({"item_uuids": []}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {intruder_token}",
    )
    assert response.status_code == 403


@pytest.mark.django_db(databases="__all__")
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
def test_article_api_crud_and_public_fetch():
    user = User.register(email="articleuser@example.com", username="articleuser")

    app = Takahe.get_or_create_app(
        "Article API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    response = client.post(
        "/api/me/article/",
        data=json.dumps(
            {
                "title": "Article Title",
                "body": "Article **body** in markdown",
                "summary": "A short summary",
                "visibility": 0,
                "tags": ["Foo", "bar"],
                "post_to_fediverse": False,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    created = response.json()
    article_uuid = created["uuid"]
    assert created["title"] == "Article Title"
    assert created["body"] == "Article **body** in markdown"
    assert created["summary"] == "A short summary"
    assert created["tags"] == ["Foo", "bar"]
    assert "<strong>body</strong>" in created["html_content"]

    response = client.get(
        "/api/me/article/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["uuid"] == article_uuid

    response = client.get(
        f"/api/me/article/{article_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200

    response = client.put(
        f"/api/me/article/{article_uuid}",
        data=json.dumps(
            {
                "title": "Article Title Updated",
                "body": "Updated body",
                "visibility": 0,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Article Title Updated"

    response = Client().get(f"/api/article/{article_uuid}")

    assert response.status_code == 200
    assert response.json()["url"].endswith(article_uuid)

    response = client.delete(
        f"/api/me/article/{article_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    response = client.get(
        f"/api/me/article/{article_uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
def test_article_api_visibility():
    owner = User.register(email="articleowner@example.com", username="articleowner")
    viewer = User.register(email="articleviewer@example.com", username="articleviewer")

    article = Article.update_local_article(
        owner=owner.identity,
        title="Private Article",
        body="secret",
        visibility=2,
    )

    owner_app = Takahe.get_or_create_app(
        "Article Owner App",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=owner.identity.pk,
    )
    owner_token = Takahe.refresh_token(owner_app, owner.identity.pk, owner.pk)
    viewer_app = Takahe.get_or_create_app(
        "Article Viewer App",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=viewer.identity.pk,
    )
    viewer_token = Takahe.refresh_token(viewer_app, viewer.identity.pk, viewer.pk)

    # owner can fetch their own private article via the public endpoint
    response = Client().get(
        f"/api/article/{article.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {owner_token}",
    )
    assert response.status_code == 200

    # another user cannot fetch a private article
    response = Client().get(
        f"/api/article/{article.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {viewer_token}",
    )
    assert response.status_code == 403

    # anonymous cannot fetch a private article
    response = Client().get(f"/api/article/{article.uuid}")
    assert response.status_code == 403

    # another user cannot reach it via the owner-scoped endpoint; it returns
    # 404 (not 403) so the endpoint can't be used to probe article existence
    response = Client().get(
        f"/api/me/article/{article.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {viewer_token}",
    )
    assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
def test_article_api_sanitizes_body_and_validates_length():
    user = User.register(email="articlesan@example.com", username="articlesan")
    app = Takahe.get_or_create_app(
        "Article Sanitize API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)
    client = Client()

    # a markdown image with a relative (invalid) src is sanitized on the API
    # path, just like the web compose form
    response = client.post(
        "/api/me/article/",
        data=json.dumps(
            {
                "title": "Sanitized",
                "body": "see ![pic](secret.png) here",
                "visibility": 0,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200
    body = response.json()["body"]
    assert "![pic](secret.png)" not in body
    assert "==[invalid image: secret.png]==" in body

    # an over-long title is rejected with a clean 422, not an uncaught DB 500
    response = client.post(
        "/api/me/article/",
        data=json.dumps(
            {
                "title": "x" * 501,
                "body": "ok",
                "visibility": 0,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 422


@pytest.mark.django_db(databases="__all__")
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

        def select_related(self, *args, **kwargs):
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


@pytest.mark.django_db(databases="__all__")
class TestApplicationOnPosts:
    """Test that posts created via API have the application field set."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="apptest@example.com", username="apptestuser")
        self.item = Edition.objects.create(title="App Test Book")
        self.app = Takahe.get_or_create_app(
            "Test App",
            "https://testapp.example.org",
            "https://testapp.example.org/callback",
            owner_pk=self.user.identity.pk,
        )
        self.token = Takahe.refresh_token(self.app, self.user.identity.pk, self.user.pk)
        self.client = Client()

    def _get_post(self, piece) -> Post:
        post_id = piece.latest_post_id
        assert post_id is not None
        post = Takahe.get_post(post_id)
        assert post is not None
        return post

    def test_mark_stores_application(self):
        response = self.client.post(
            f"/api/me/shelf/item/{self.item.uuid}",
            data=json.dumps({"shelf_type": "wishlist", "visibility": 0, "tags": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        assert response.status_code == 200

        mark = Mark(self.user.identity, self.item)
        post = self._get_post(mark.shelfmember)
        assert post.application_id == self.app.pk

        mastodon_json = post.to_mastodon_json()
        assert mastodon_json["application"] == {
            "name": "Test App",
            "website": "https://testapp.example.org",
        }

    def test_review_stores_application(self):
        response = self.client.post(
            f"/api/me/review/item/{self.item.uuid}",
            data=json.dumps(
                {
                    "title": "Review Title",
                    "body": "Review Body",
                    "visibility": 0,
                    "post_to_fediverse": False,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        assert response.status_code == 200

        review = Review.objects.get(owner=self.user.identity, item=self.item)
        post = self._get_post(review)
        assert post.application_id == self.app.pk

        mastodon_json = post.to_mastodon_json()
        assert mastodon_json["application"] == {
            "name": "Test App",
            "website": "https://testapp.example.org",
        }

    def test_note_stores_application(self):
        response = self.client.post(
            f"/api/me/note/item/{self.item.uuid}/",
            data=json.dumps(
                {
                    "title": "Note Title",
                    "content": "Note Content",
                    "sensitive": False,
                    "visibility": 0,
                    "post_to_fediverse": False,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        assert response.status_code == 200

        note = Note.objects.get(owner=self.user.identity, item=self.item)
        post = self._get_post(note)
        assert post.application_id == self.app.pk

        mastodon_json = post.to_mastodon_json()
        assert mastodon_json["application"] == {
            "name": "Test App",
            "website": "https://testapp.example.org",
        }

    def test_collection_stores_application(self):
        response = self.client.post(
            "/api/me/collection/",
            data=json.dumps(
                {"title": "App Collection", "brief": "desc", "visibility": 0}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        assert response.status_code == 200
        collection_uuid = response.json()["uuid"]

        collection = Collection.get_by_url(collection_uuid)
        post = self._get_post(collection)
        assert post.application_id == self.app.pk

        mastodon_json = post.to_mastodon_json()
        assert mastodon_json["application"] == {
            "name": "Test App",
            "website": "https://testapp.example.org",
        }

    def test_post_without_application(self):
        """Posts created outside API should have application=None."""
        mark = Mark(self.user.identity, self.item)
        mark.update(ShelfType.WISHLIST, visibility=0)

        mark = Mark(self.user.identity, self.item)
        post = self._get_post(mark.shelfmember)
        assert post.application_id is None

        mastodon_json = post.to_mastodon_json()
        assert mastodon_json["application"] is None


@pytest.mark.django_db(databases="__all__")
def test_optional_auth_on_public_endpoints():
    """Test that public-read endpoints accept optional Bearer tokens."""
    with (
        patch("catalog.models.item.Item.update_index"),
        patch("journal.models.collection.Collection.sync_to_timeline"),
        patch("journal.models.collection.Collection.update_index"),
    ):
        owner = User.register(email="optauth@example.com", username="optauthowner")
        viewer = User.register(email="optviewer@example.com", username="optauthviewer")
        item = Edition.objects.create(title="OptAuth Book")

        public_collection = Collection.objects.create(
            owner=owner.identity,
            title="Public Collection",
            brief="",
            visibility=0,
        )
        follower_collection = Collection.objects.create(
            owner=owner.identity,
            title="Follower Collection",
            brief="",
            visibility=1,
        )
        public_collection.append_item(item, note="")
        follower_collection.append_item(item, note="")

    Review.update_item_review(
        item, owner.identity, "Public Review", "body", visibility=0
    )
    review = Review.objects.get(owner=owner.identity, item=item)

    viewer.identity.follow(owner.identity, True)
    app = Takahe.get_or_create_app(
        "OptAuth API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=viewer.identity.pk,
    )
    token = Takahe.refresh_token(app, viewer.identity.pk, viewer.pk)
    anon = Client()
    authed = Client()

    # 1. Authenticated user sees public collection
    response = authed.get(
        f"/api/collection/{public_collection.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200
    assert response.json()["uuid"] == public_collection.uuid

    # 2. Authenticated follower sees follower-only collection
    response = authed.get(
        f"/api/collection/{follower_collection.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200
    assert response.json()["uuid"] == follower_collection.uuid

    # 3. Authenticated follower sees follower-only collection items
    response = authed.get(
        f"/api/collection/{follower_collection.uuid}/item/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200

    # 4. Anonymous user sees public collection
    response = anon.get(f"/api/collection/{public_collection.uuid}")
    assert response.status_code == 200

    # 5. Anonymous user gets 403 for follower-only collection
    response = anon.get(f"/api/collection/{follower_collection.uuid}")
    assert response.status_code == 403

    # 6. Authenticated user sees review via public endpoint
    response = authed.get(
        f"/api/review/{review.uuid}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200

    # 7. Anonymous user sees public review
    response = anon.get(f"/api/review/{review.uuid}")
    assert response.status_code == 200

    # 8. Anonymous user can access /item/{uuid}/collection/ (public only)
    response = anon.get(f"/api/item/{item.uuid}/collection/")
    assert response.status_code == 200
    payload = response.json()
    uuids = {c["uuid"] for c in payload["data"]}
    assert public_collection.uuid in uuids
    assert follower_collection.uuid not in uuids

    # 9. Authenticated follower sees more collections for item
    response = authed.get(
        f"/api/item/{item.uuid}/collection/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200
    payload = response.json()
    uuids = {c["uuid"] for c in payload["data"]}
    assert public_collection.uuid in uuids
    assert follower_collection.uuid in uuids

    # 10. Invalid token returns 401 on optional-auth endpoints
    response = anon.get(
        f"/api/collection/{public_collection.uuid}",
        HTTP_AUTHORIZATION="Bearer invalidtoken123",
    )
    assert response.status_code == 401

    response = anon.get(
        f"/api/review/{review.uuid}",
        HTTP_AUTHORIZATION="Bearer invalidtoken123",
    )
    assert response.status_code == 401

    response = anon.get(
        f"/api/item/{item.uuid}/collection/",
        HTTP_AUTHORIZATION="Bearer invalidtoken123",
    )
    assert response.status_code == 401


@pytest.mark.django_db(databases="__all__")
def test_posts_endpoint_optional_auth():
    """Test that /item/{uuid}/posts/ works with and without auth."""
    user = User.register(email="postopt@example.com", username="postoptuser")
    item = Edition.objects.create(title="PostOpt Item")

    app = Takahe.get_or_create_app(
        "PostOpt API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    token = Takahe.refresh_token(app, user.identity.pk, user.pk)

    class StubPosts(list):
        def prefetch_related(self, *args, **kwargs):
            return self

        def select_related(self, *args, **kwargs):
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

    stub_posts = StubPosts([StubPost(1)])
    stub_result = StubResult(stub_posts)

    with patch(
        "journal.apis.post.JournalIndex.instance", return_value=StubIndex(stub_result)
    ):
        # Authenticated access works
        response = Client().get(
            f"/api/item/{item.uuid}/posts/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 200
        assert response.json()["count"] == 1

        # Anonymous access works
        response = Client().get(f"/api/item/{item.uuid}/posts/")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        # Invalid token returns 401
        response = Client().get(
            f"/api/item/{item.uuid}/posts/",
            HTTP_AUTHORIZATION="Bearer invalidtoken",
        )
        assert response.status_code == 401


@pytest.mark.django_db(databases="__all__")
def test_collection_trending_excludes_non_public():
    cache.clear()
    owner = User.register(email="trend2@example.com", username="trenduser2")
    public_collection = Collection.objects.create(
        owner=owner.identity,
        title="Public Trending",
        brief="",
        visibility=0,
    )
    private_collection = Collection.objects.create(
        owner=owner.identity,
        title="Private Trending",
        brief="",
        visibility=2,
    )
    # simulate a stale discover-job cache that still lists a collection
    # whose owner made it non-public afterwards
    cache.set(
        "featured_collections",
        [public_collection.pk, private_collection.pk],
        timeout=None,
    )

    response = Client().get("/api/trending/collection/")

    assert response.status_code == 200
    uuids = [c["uuid"] for c in response.json()]
    assert public_collection.uuid in uuids
    assert private_collection.uuid not in uuids


@pytest.mark.django_db(databases="__all__")
def test_featured_collection_hidden_after_owner_restricts():
    owner = User.register(email="fcowner@example.com", username="fcowner")
    viewer = User.register(email="fcviewer@example.com", username="fcviewer")
    collection = Collection.objects.create(
        owner=owner.identity,
        title="Was Public",
        brief="",
        visibility=0,
    )
    FeaturedCollection.objects.create(owner=viewer.identity, target=collection)

    app = Takahe.get_or_create_app(
        "Featured Visibility Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=viewer.identity.pk,
    )
    token = Takahe.refresh_token(app, viewer.identity.pk, viewer.pk)
    client = Client()

    response = client.get(
        "/api/me/collection/featured/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200
    assert [c["uuid"] for c in response.json()] == [collection.uuid]

    collection.visibility = 2
    collection.save(update_fields=["visibility"])

    response = client.get(
        "/api/me/collection/featured/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200
    assert response.json() == []
