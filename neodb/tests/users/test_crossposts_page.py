import pytest
from django.test import Client
from django.urls import reverse

from catalog.models import Edition
from journal.models import CrosspostRetry, Mark, ShelfType
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _member(username: str) -> tuple[User, Client]:
    user = User.register(email=f"{username}@example.com", username=username)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, client


def test_failed_crossposts_render_as_cards():
    user, client = _member("xposter")
    book = Edition.objects.create(title="Crossposted Book")
    mark = Mark(user.identity, book)
    mark.update(ShelfType.COMPLETE, visibility=0)
    retry = CrosspostRetry.objects.create(
        user=user,
        piece=mark.shelfmember,
        platform="mastodon",
        error_type=CrosspostRetry.ErrorType.other,
        message="Upstream returned 502.",
    )

    content = client.get(reverse("users:crossposts")).content.decode()

    assert 'class="dc-mh"' in content
    assert f'id="crosspost-row-{retry.pk}"' in content
    assert "Crossposted Book" in content
    assert "Upstream returned 502." in content
    assert reverse("users:crosspost_retry", args=[retry.pk]) in content
    assert "<table" not in content


def test_no_failed_crossposts_shows_empty_state():
    _, client = _member("cleanposter")
    content = client.get(reverse("users:crossposts")).content.decode()
    assert 'class="dc-empty"' in content
    assert "No failed crossposts." in content
