from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from catalog.models import Edition, Game, Movie
from journal.models import Collection, FeaturedCollection, Mark
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
