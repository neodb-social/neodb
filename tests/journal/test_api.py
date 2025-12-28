import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from catalog.models import Edition, Game, Movie
from journal.models import Mark
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
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    }
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
