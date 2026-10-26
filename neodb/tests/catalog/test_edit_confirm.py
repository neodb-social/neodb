import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from catalog.models import (
    Edition,
    ExternalResource,
    IdType,
    Item,
    Movie,
    Podcast,
    TVSeason,
    TVShow,
    Work,
)
from journal.models import Review
from users.models import User


def _login(client: Client, is_staff: bool = False, username: str = "editor") -> User:
    user = User.register(email=f"{username}@example.com", username=username)
    if is_staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user


def _podcast(title: str = "Pocket Casts") -> Podcast:
    return Podcast.objects.create(
        localized_title=[{"lang": "en", "text": title}],
        host=["Alice"],
        official_site="https://example.org/pc",
    )


def _review(item, owner_username: str = "reviewer") -> Review:
    owner = User.register(
        email=f"{owner_username}@example.com", username=owner_username
    )
    return Review.objects.create(
        owner=owner.identity, item=item, title="t", body="b", visibility=0
    )


def _podcast_form_data(title: str, host: list[str] | None = None) -> dict:
    return {
        "localized_title": json.dumps([{"lang": "en", "text": title}]),
        "host": json.dumps(["Alice"] if host is None else host),
        "language": "[]",
        "genre": "[]",
        "localized_description": "[]",
        "official_site": "https://example.org/pc",
        "primary_lookup_id_type": "",
        "primary_lookup_id_value": "",
    }


@pytest.mark.django_db(databases="__all__")
class TestEditPreview:
    def test_preview_shows_diff_and_saves_nothing(self):
        item = _podcast()
        client = Client()
        _login(client)

        response = client.post(f"{item.url}/edit", _podcast_form_data("Renamed"))

        assert response.status_code == 200
        body = response.content.decode()
        assert "Confirm changes" in body
        assert "Pocket Casts" in body
        assert "Renamed" in body
        assert "Confirm and save" in body
        assert Podcast.objects.get(pk=item.pk).display_title == "Pocket Casts"

    def test_preview_with_no_changes(self):
        item = _podcast()
        client = Client()
        _login(client)

        response = client.post(f"{item.url}/edit", _podcast_form_data("Pocket Casts"))

        assert response.status_code == 200
        assert "No changes detected" in response.content.decode()

    def test_preview_reports_validation_errors(self):
        item = _podcast()
        client = Client()
        _login(client)

        response = client.post(
            f"{item.url}/edit", _podcast_form_data("Renamed", host=[])
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert "Please correct the errors below" in body
        assert "Confirm and save" not in body
        assert Podcast.objects.get(pk=item.pk).display_title == "Pocket Casts"

    def test_confirmed_post_saves(self):
        item = _podcast()
        client = Client()
        _login(client)

        response = client.post(
            f"{item.url}/edit", {**_podcast_form_data("Renamed"), "sure": "1"}
        )

        assert response.status_code == 302
        assert Podcast.objects.get(pk=item.pk).display_title == "Renamed"

    def test_confirmed_invalid_post_is_rejected(self):
        item = _podcast()
        client = Client()
        _login(client)

        response = client.post(
            f"{item.url}/edit", {**_podcast_form_data("Renamed", host=[]), "sure": "1"}
        )

        assert response.status_code == 400
        assert Podcast.objects.get(pk=item.pk).display_title == "Pocket Casts"


@pytest.mark.django_db(databases="__all__")
class TestSidebarConfirmPages:
    def test_delete_asks_first(self):
        item = _podcast()
        client = Client()
        _login(client)

        response = client.post(f"{item.url}/delete")

        assert response.status_code == 200
        assert "Are you sure to delete?" in response.content.decode()
        item.refresh_from_db()
        assert not item.is_deleted

    def test_merge_asks_first(self):
        item = _podcast()
        target = _podcast("Other Casts")
        client = Client()
        _login(client)

        response = client.post(
            f"{item.url}/merge", {"target_item_url": target.absolute_url}
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert "Are you sure to merge?" in body
        assert "Other Casts" in body
        item.refresh_from_db()
        assert item.merged_to_item_id is None

    def test_recast_asks_first_then_recasts(self):
        movie = Movie.objects.create(title="Web Movie")
        client = Client()
        _login(client)

        response = client.post(f"{movie.url}/recast", {"class": "tvshow"})
        assert response.status_code == 200
        body = response.content.decode()
        assert "Are you sure to switch category?" in body
        assert "Switching may remove some metadata." in body
        assert isinstance(Item.objects.get(pk=movie.pk), Movie)

        response = client.post(f"{movie.url}/recast", {"class": "tvshow", "sure": "1"})
        assert response.status_code == 302
        assert isinstance(Item.objects.get(pk=movie.pk), TVShow)

    def test_recast_lists_seasons_to_detach(self):
        show = TVShow.objects.create(title="Web Show")
        season = TVSeason.objects.create(title="Season One", show=show, season_number=1)
        client = Client()
        _login(client, is_staff=True)

        response = client.post(f"{show.url}/recast", {"class": "movie"})

        assert response.status_code == 200
        body = response.content.decode()
        assert "will be detached" in body
        assert season.url in body
        season.refresh_from_db()
        assert season.show == show

    def test_assign_parent_asks_first_then_links(self):
        show = TVShow.objects.create(title="Web Show")
        season = TVSeason.objects.create(title="Orphan Season", season_number=1)
        client = Client()
        _login(client)

        response = client.post(
            f"{season.url}/assign_parent", {"parent_item_url": show.absolute_url}
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert "Are you sure to link?" in body
        assert "Web Show" in body
        season.refresh_from_db()
        assert season.show is None

        response = client.post(
            f"{season.url}/assign_parent",
            {"parent_item_url": show.absolute_url, "sure": "1"},
        )
        assert response.status_code == 302
        season.refresh_from_db()
        assert season.show == show

    def test_remove_unused_seasons_asks_first_then_deletes(self):
        show = TVShow.objects.create(title="Web Show")
        used = TVSeason.objects.create(title="Used Season", show=show, season_number=1)
        unused = TVSeason.objects.create(
            title="Unused Season", show=show, season_number=2
        )
        _review(used)
        client = Client()
        _login(client)

        response = client.post(f"{show.url}/remove_unused_seasons")
        assert response.status_code == 200
        body = response.content.decode()
        assert "will be deleted" in body
        assert unused.url in body
        assert used.url in body
        unused.refresh_from_db()
        assert not unused.is_deleted

        response = client.post(f"{show.url}/remove_unused_seasons", {"sure": "1"})
        assert response.status_code == 302
        unused.refresh_from_db()
        used.refresh_from_db()
        assert unused.is_deleted
        assert not used.is_deleted

    def test_unlink_works_asks_first_then_unlinks(self):
        edition = Edition.objects.create(title="Hyperion")
        work = Work.objects.create(title="Hyperion Cantos")
        work.editions.add(edition)
        client = Client()
        _login(client)

        response = client.post(f"{edition.url}/unlink_works")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Are you sure to unlink?" in body
        assert work.url in body
        assert edition.get_work() == work

        response = client.post(f"{edition.url}/unlink_works", {"sure": "1"})
        assert response.status_code == 302
        assert edition.get_work() is None

    def test_unlink_resource_asks_first_then_unlinks(self):
        edition = Edition.objects.create(title="Hyperion")
        resource = ExternalResource.objects.create(
            item=edition,
            id_type=IdType.Goodreads,
            id_value="12345",
            url="https://www.goodreads.com/book/show/12345",
            scraped_time=timezone.now(),
            metadata={"title": "Hyperion"},
        )
        client = Client()
        _login(client, is_staff=True)
        unlink_url = reverse("catalog:unlink")

        response = client.post(unlink_url, {"id": resource.pk})
        assert response.status_code == 200
        body = response.content.decode()
        assert "remove the link to this site" in body
        assert resource.url in body
        resource.refresh_from_db()
        assert resource.item == edition

        response = client.post(
            unlink_url,
            {"id": resource.pk, "sure": "1", "next": f"{edition.url}/edit"},
        )
        assert response.status_code == 302
        assert response["Location"] == f"{edition.url}/edit"
        resource.refresh_from_db()
        assert resource.item is None

    def test_unlink_resource_ignores_foreign_next(self):
        edition = Edition.objects.create(title="Hyperion")
        resource = ExternalResource.objects.create(
            item=edition,
            id_type=IdType.Goodreads,
            id_value="12345",
            url="https://www.goodreads.com/book/show/12345",
            scraped_time=timezone.now(),
            metadata={},
        )
        client = Client()
        _login(client, is_staff=True)

        response = client.post(
            reverse("catalog:unlink"),
            {"id": resource.pk, "sure": "1", "next": "https://evil.example/x"},
        )

        assert response.status_code == 302
        assert response["Location"] == "/"


@pytest.mark.django_db(databases="__all__")
class TestEditPermissionGaps:
    def test_recast_rejects_a_source_outside_the_video_classes(self):
        book = Edition.objects.create(title="A Book")
        client = Client()
        _login(client)

        response = client.post(f"{book.url}/recast", {"class": "movie", "sure": "1"})

        assert response.status_code == 400
        assert isinstance(Item.objects.get(pk=book.pk), Edition)

    def test_non_staff_cannot_recast_a_show_with_seasons(self):
        show = TVShow.objects.create(title="Web Show")
        season = TVSeason.objects.create(title="Season One", show=show, season_number=1)
        client = Client()
        _login(client)

        response = client.post(f"{show.url}/recast", {"class": "movie", "sure": "1"})

        assert response.status_code == 403
        season.refresh_from_db()
        assert season.show == show
        assert isinstance(Item.objects.get(pk=show.pk), TVShow)

    def test_non_staff_cannot_touch_a_merged_or_deleted_item(self):
        source = Movie.objects.create(title="Dup")
        target = Movie.objects.create(title="Canonical")
        source.merge_to(target)
        gone = Movie.objects.create(title="Gone")
        gone.delete()
        client = Client()
        user = _login(client)

        assert not source.is_editable_by(user)
        assert not gone.is_editable_by(user)
        response = client.post(f"{source.url}/merge", {"sure": "1"})
        assert response.status_code == 403
        source.refresh_from_db()
        assert source.merged_to_item == target

        staff_client = Client()
        staff = _login(staff_client, is_staff=True, username="boss")
        assert source.is_editable_by(staff)

    def test_fetch_tvepisodes_requires_edit_permission(self):
        show = TVShow.objects.create(title="Locked Show")
        season = TVSeason.objects.create(
            title="Locked Season",
            show=show,
            season_number=1,
            is_protected=True,
        )
        client = Client()
        _login(client)

        response = client.post(f"{season.url}/fetch_tvepisodes")

        assert response.status_code == 403

    def test_pick_cover_rejects_a_non_decimal_resource_id(self):
        movie = Movie.objects.create(title="Cover Movie")
        client = Client()
        _login(client)

        response = client.post(f"{movie.url}/pick_cover", {"resource_id": "²"})

        assert response.status_code == 400

    def test_credit_names_are_length_checked(self):
        movie = Movie.objects.create(title="Credit Movie")
        client = Client()
        _login(client)

        response = client.post(
            f"{movie.url}/credits/add", {"role": "director", "name": "x" * 1001}
        )
        assert response.status_code == 400

        response = client.post(
            f"{movie.url}/credits/add", {"role": "director", "name": "Someone"}
        )
        assert response.status_code in (200, 302)
        credit = movie.credits.get()
        response = client.post(
            f"{movie.url}/credits/{credit.pk}/update", {"character_name": "y" * 501}
        )
        assert response.status_code == 400
