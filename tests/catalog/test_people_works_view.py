import pytest
from django.test import Client

from catalog.models import (
    Edition,
    ItemPeopleRelation,
    People,
    PeopleRole,
    PeopleType,
    TVEpisode,
    TVSeason,
    TVShow,
)
from catalog.models.book import Work
from journal.models import ShelfMember, ShelfType
from users.models import User


def _author(name: str = "Dan Simmons") -> People:
    return People.objects.create(
        metadata={"localized_name": [{"lang": "en", "text": name}]},
        people_type=PeopleType.PERSON,
    )


def _director(name: str = "Jane Director") -> People:
    return People.objects.create(
        metadata={"localized_name": [{"lang": "en", "text": name}]},
        people_type=PeopleType.PERSON,
    )


@pytest.mark.django_db(databases="__all__")
class TestPeopleWorksHidesChildren:
    def test_hides_edition_when_work_credited(self):
        person = _author()
        work = Work.objects.create(title="Hyperion")
        edition = Edition.objects.create(title="Hyperion (1989)")
        work.editions.add(edition)
        ItemPeopleRelation.objects.create(
            item=work, people=person, role=PeopleRole.AUTHOR
        )
        ItemPeopleRelation.objects.create(
            item=edition, people=person, role=PeopleRole.AUTHOR
        )

        response = Client().get(f"{person.url}/works/{PeopleRole.AUTHOR.value}")
        assert response.status_code == 200
        works_page = response.context["works"]
        ids = {w.pk for w in works_page.object_list}
        assert work.pk in ids
        assert edition.pk not in ids
        assert response.context["total"] == 1

    def test_shows_edition_when_work_soft_deleted(self):
        """Regression: a soft-deleted parent must not hide an active child."""
        person = _author()
        work = Work.objects.create(title="Hyperion")
        edition = Edition.objects.create(title="Hyperion (1989)")
        work.editions.add(edition)
        ItemPeopleRelation.objects.create(
            item=work, people=person, role=PeopleRole.AUTHOR
        )
        ItemPeopleRelation.objects.create(
            item=edition, people=person, role=PeopleRole.AUTHOR
        )
        work.delete(soft=True)

        response = Client().get(f"{person.url}/works/{PeopleRole.AUTHOR.value}")
        assert response.status_code == 200
        ids = {w.pk for w in response.context["works"].object_list}
        assert edition.pk in ids
        assert work.pk not in ids

    def test_hides_tvseason_when_tvshow_credited(self):
        person = _director()
        show = TVShow.objects.create(title="Show")
        season = TVSeason.objects.create(title="Show S1", show=show, season_number=1)
        ItemPeopleRelation.objects.create(
            item=show, people=person, role=PeopleRole.DIRECTOR
        )
        ItemPeopleRelation.objects.create(
            item=season, people=person, role=PeopleRole.DIRECTOR
        )

        response = Client().get(f"{person.url}/works/{PeopleRole.DIRECTOR.value}")
        assert response.status_code == 200
        ids = {w.pk for w in response.context["works"].object_list}
        assert show.pk in ids
        assert season.pk not in ids

    def test_hides_tvepisode_via_grandparent(self):
        """A TVEpisode is hidden when its grandparent TVShow is credited,
        even if the intermediate TVSeason is not."""
        person = _director()
        show = TVShow.objects.create(title="Show")
        season = TVSeason.objects.create(title="Show S1", show=show, season_number=1)
        episode = TVEpisode.objects.create(
            title="Show S1E1", season=season, episode_number=1
        )
        ItemPeopleRelation.objects.create(
            item=show, people=person, role=PeopleRole.DIRECTOR
        )
        ItemPeopleRelation.objects.create(
            item=episode, people=person, role=PeopleRole.DIRECTOR
        )

        response = Client().get(f"{person.url}/works/{PeopleRole.DIRECTOR.value}")
        assert response.status_code == 200
        ids = {w.pk for w in response.context["works"].object_list}
        assert show.pk in ids
        assert episode.pk not in ids

    def test_standalone_tvepisode_is_shown(self):
        """A TVEpisode with no credited ancestor must still be displayed."""
        person = _director()
        show = TVShow.objects.create(title="Show")
        season = TVSeason.objects.create(title="Show S1", show=show, season_number=1)
        episode = TVEpisode.objects.create(
            title="Show S1E1", season=season, episode_number=1
        )
        ItemPeopleRelation.objects.create(
            item=episode, people=person, role=PeopleRole.DIRECTOR
        )

        response = Client().get(f"{person.url}/works/{PeopleRole.DIRECTOR.value}")
        assert response.status_code == 200
        ids = {w.pk for w in response.context["works"].object_list}
        assert episode.pk in ids

    def test_status_filter_does_not_hide_child_of_excluded_parent(self):
        """Regression: when the status filter drops the parent, the child
        must remain visible rather than being hidden for redundancy."""
        person = _author()
        work = Work.objects.create(title="Hyperion")
        edition = Edition.objects.create(title="Hyperion (1989)")
        work.editions.add(edition)
        ItemPeopleRelation.objects.create(
            item=work, people=person, role=PeopleRole.AUTHOR
        )
        ItemPeopleRelation.objects.create(
            item=edition, people=person, role=PeopleRole.AUTHOR
        )

        user = User.register(email="reader@example.com", username="reader")
        shelf = user.identity.shelf_manager.get_shelf(ShelfType.COMPLETE)
        ShelfMember.objects.create(
            owner=user.identity,
            item=edition,
            parent=shelf,
            visibility=0,
            position=0,
        )
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")

        response = client.get(
            f"{person.url}/works/{PeopleRole.AUTHOR.value}?status={ShelfType.COMPLETE.value}"
        )
        assert response.status_code == 200
        ids = {w.pk for w in response.context["works"].object_list}
        assert edition.pk in ids
        assert work.pk not in ids
