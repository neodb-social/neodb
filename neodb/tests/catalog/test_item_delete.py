import pytest
from django.test import Client

from catalog.models import Podcast
from journal.models import Review
from users.models import User


def _login(client: Client, is_staff: bool = False, username: str = "deleter") -> User:
    user = User.register(email=f"{username}@example.com", username=username)
    if is_staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user


def _podcast(title: str = "Pocket Casts") -> Podcast:
    return Podcast.objects.create(
        localized_title=[{"lang": "en", "text": title}],
        official_site="https://example.org/pc",
    )


def _review(item, owner_username: str = "reviewer") -> Review:
    owner = User.register(
        email=f"{owner_username}@example.com", username=owner_username
    )
    return Review.objects.create(
        owner=owner.identity, item=item, title="t", body="b", visibility=0
    )


@pytest.mark.django_db(databases="__all__")
class TestItemDeleteInUse:
    def test_staff_cannot_delete_item_in_use(self):
        """Deleting an in-use item strands every piece pointing at it: the
        piece can be exported but never re-imported (NEODB-SOCIAL-7VV)."""
        item = _podcast()
        _review(item)
        client = Client()
        _login(client, is_staff=True)

        response = client.post(f"{item.url}/delete", {"sure": "1"})
        assert response.status_code == 403
        item.refresh_from_db()
        assert not item.is_deleted

    def test_non_staff_cannot_delete_item_in_use(self):
        item = _podcast()
        _review(item)
        client = Client()
        _login(client)

        response = client.post(f"{item.url}/delete", {"sure": "1"})
        assert response.status_code == 403
        item.refresh_from_db()
        assert not item.is_deleted

    def test_staff_can_still_delete_unused_item(self):
        item = _podcast()
        client = Client()
        _login(client, is_staff=True)

        response = client.post(f"{item.url}/delete", {"sure": "1"})
        assert response.status_code == 302
        item.refresh_from_db()
        assert item.is_deleted

    def test_sidebar_offers_merge_but_not_delete_for_item_in_use(self):
        """Staff must not be shown a button that can only 403. Merge stays,
        since it keeps the pieces reachable."""
        item = _podcast()
        _review(item)
        client = Client()
        _login(client, is_staff=True)

        content = client.get(f"{item.url}/edit").content.decode()
        assert f"{item.url}/merge" in content
        assert f"{item.url}/delete" not in content

    def test_sidebar_offers_delete_for_unused_item(self):
        item = _podcast()
        client = Client()
        _login(client, is_staff=True)

        content = client.get(f"{item.url}/edit").content.decode()
        assert f"{item.url}/delete" in content
