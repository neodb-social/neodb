import pytest
from django.test import Client
from django.utils import timezone

from catalog.models import Edition, ExternalResource, IdType
from users.models import User


def _make_edition_with_resource(cover: str = "item/test/cover.jpg") -> Edition:
    edition = Edition.objects.create(title="Hyperion")
    ExternalResource.objects.create(
        item=edition,
        id_type=IdType.Goodreads,
        id_value="12345",
        url="https://www.goodreads.com/book/show/12345",
        scraped_time=timezone.now(),
        metadata={"title": "Hyperion"},
        cover=cover,
    )
    return edition


def _login(client: Client, is_staff: bool = False) -> User:
    user = User.register(email="editor@example.com", username="editor")
    if is_staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user


@pytest.mark.django_db(databases="__all__")
class TestPickCover:
    def test_pick_cover_from_linked_resource(self):
        edition = _make_edition_with_resource()
        resource = edition.external_resources.get()
        client = Client()
        _login(client)

        response = client.post(
            f"{edition.url}/pick_cover", {"resource_id": resource.pk}
        )
        assert response.status_code == 302
        assert response.headers["Location"] == edition.url
        edition.refresh_from_db()
        assert edition.cover.name == resource.cover.name

    def test_pick_current_cover_keeps_existing(self):
        edition = _make_edition_with_resource()
        edition.cover = "item/test/original.jpg"
        edition.save(update_fields=["cover"])
        client = Client()
        _login(client)

        response = client.post(f"{edition.url}/pick_cover", {"resource_id": "current"})
        assert response.status_code == 302
        assert response.headers["Location"] == edition.url
        edition.refresh_from_db()
        assert edition.cover.name == "item/test/original.jpg"

    def test_edit_page_lists_resource_covers(self):
        edition = _make_edition_with_resource()
        resource = edition.external_resources.get()
        client = Client()
        _login(client)

        response = client.get(f"{edition.url}/edit")
        assert response.status_code == 200
        assert [r.pk for r in response.context["resource_covers"]] == [resource.pk]

    def test_edit_page_skips_resources_without_cover(self):
        edition = _make_edition_with_resource(cover="")
        client = Client()
        _login(client)

        response = client.get(f"{edition.url}/edit")
        assert response.status_code == 200
        assert response.context["resource_covers"] == []

    def test_resource_not_linked_to_item_returns_404(self):
        edition = _make_edition_with_resource()
        other = Edition.objects.create(title="Endymion")
        resource = edition.external_resources.get()
        client = Client()
        _login(client)

        response = client.post(f"{other.url}/pick_cover", {"resource_id": resource.pk})
        assert response.status_code == 404
        other.refresh_from_db()
        assert not other.has_cover()

    def test_resource_without_cover_returns_400(self):
        edition = _make_edition_with_resource(cover="")
        resource = edition.external_resources.get()
        client = Client()
        _login(client)

        response = client.post(
            f"{edition.url}/pick_cover", {"resource_id": resource.pk}
        )
        assert response.status_code == 400

    def test_missing_resource_id_returns_400(self):
        edition = _make_edition_with_resource()
        client = Client()
        _login(client)

        response = client.post(f"{edition.url}/pick_cover", {})
        assert response.status_code == 400

    def test_nonnumeric_resource_id_returns_400(self):
        edition = _make_edition_with_resource()
        client = Client()
        _login(client)

        response = client.post(f"{edition.url}/pick_cover", {"resource_id": "abc"})
        assert response.status_code == 400

    def test_protected_item_requires_staff(self):
        edition = _make_edition_with_resource()
        edition.is_protected = True
        edition.save(update_fields=["is_protected"])
        resource = edition.external_resources.get()
        client = Client()
        _login(client)

        response = client.post(
            f"{edition.url}/pick_cover", {"resource_id": resource.pk}
        )
        assert response.status_code == 403
        edition.refresh_from_db()
        assert not edition.has_cover()

    def test_protected_item_editable_by_staff(self):
        edition = _make_edition_with_resource()
        edition.is_protected = True
        edition.save(update_fields=["is_protected"])
        resource = edition.external_resources.get()
        client = Client()
        _login(client, is_staff=True)

        response = client.post(
            f"{edition.url}/pick_cover", {"resource_id": resource.pk}
        )
        assert response.status_code == 302
        edition.refresh_from_db()
        assert edition.cover.name == resource.cover.name
