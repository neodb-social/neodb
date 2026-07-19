import pytest
from django.test import Client

from catalog.models import (
    Edition,
    ItemCredit,
    Movie,
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


def _credit(item, person: People, role) -> ItemCredit:
    """Create the ItemCredit link the people-works view now reads from."""
    return ItemCredit.objects.create(
        item=item, person=person, role=role, name=person.display_name
    )


@pytest.mark.django_db(databases="__all__")
class TestPeopleWorksHidesChildren:
    def test_hides_edition_when_work_credited(self):
        person = _author()
        work = Work.objects.create(title="Hyperion")
        edition = Edition.objects.create(title="Hyperion (1989)")
        work.editions.add(edition)
        _credit(work, person, PeopleRole.AUTHOR)
        _credit(edition, person, PeopleRole.AUTHOR)

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
        _credit(work, person, PeopleRole.AUTHOR)
        _credit(edition, person, PeopleRole.AUTHOR)
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
        _credit(show, person, PeopleRole.DIRECTOR)
        _credit(season, person, PeopleRole.DIRECTOR)

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
        _credit(show, person, PeopleRole.DIRECTOR)
        _credit(episode, person, PeopleRole.DIRECTOR)

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
        _credit(episode, person, PeopleRole.DIRECTOR)

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
        _credit(work, person, PeopleRole.AUTHOR)
        _credit(edition, person, PeopleRole.AUTHOR)

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


@pytest.mark.django_db(databases="__all__")
class TestPeopleWorksMergedAndDeleted:
    def test_merged_people_works_redirects_to_target(self):
        person1 = _author("Dan Simmons")
        person2 = _author("Daniel Simmons")
        person1.merge_to(person2)

        response = Client().get(f"{person1.url}/works/{PeopleRole.AUTHOR.value}")
        assert response.status_code == 302
        assert response.headers["Location"] == (
            f"{person2.url}/works/{PeopleRole.AUTHOR.value}"
        )

    def test_chained_merge_redirects_to_final_target_in_one_hop(self):
        person1 = _author("Dan Simmons")
        person2 = _author("Daniel Simmons")
        person3 = _author("D. Simmons")
        person1.merge_to(person2)
        person2.merge_to(person3)

        response = Client().get(f"{person1.url}/works/{PeopleRole.AUTHOR.value}")
        assert response.status_code == 302
        assert response.headers["Location"] == (
            f"{person3.url}/works/{PeopleRole.AUTHOR.value}"
        )

    def test_deleted_people_works_returns_404(self):
        person = _author()
        person.delete(soft=True)

        response = Client().get(f"{person.url}/works/{PeopleRole.AUTHOR.value}")
        assert response.status_code == 404

    def test_merged_to_deleted_target_returns_404_without_redirect(self):
        person1 = _author("Dan Simmons")
        person2 = _author("Daniel Simmons")
        person1.merge_to(person2)
        person2.delete(soft=True)

        response = Client().get(f"{person1.url}/works/{PeopleRole.AUTHOR.value}")
        assert response.status_code == 404

    def test_invalid_role_on_merged_people_returns_404_without_redirect(self):
        person1 = _author("Dan Simmons")
        person2 = _author("Daniel Simmons")
        person1.merge_to(person2)

        response = Client().get(f"{person1.url}/works/not_a_role")
        assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
class TestPeopleWorksMarks:
    """The works page shows the viewer's shelf status on each listed work."""

    def _setup_marked_work(self) -> tuple[People, Movie, Movie, User]:
        person = _director()
        watched = Movie.objects.create(title="Watched Movie")
        unwatched = Movie.objects.create(title="Unwatched Movie")
        for movie in (watched, unwatched):
            _credit(movie, person, PeopleRole.DIRECTOR)
        user = User.register(email="viewer@example.com", username="viewer")
        shelf = user.identity.shelf_manager.get_shelf(ShelfType.COMPLETE)
        ShelfMember.objects.create(
            owner=user.identity,
            item=watched,
            parent=shelf,
            visibility=0,
            position=0,
        )
        return person, watched, unwatched, user

    def test_people_works_attaches_marks_in_bulk(self):
        person, watched, unwatched, user = self._setup_marked_work()
        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")

        response = client.get(f"{person.url}/works/{PeopleRole.DIRECTOR.value}")
        assert response.status_code == 200
        marks = {w.pk: getattr(w, "mark", None) for w in response.context["works"]}
        watched_mark = marks[watched.pk]
        unwatched_mark = marks[unwatched.pk]
        assert watched_mark is not None
        assert watched_mark.shelf_type == ShelfType.COMPLETE
        assert unwatched_mark is not None
        assert unwatched_mark.shelf_type is None
