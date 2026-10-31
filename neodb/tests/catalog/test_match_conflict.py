import pytest
from django.utils import timezone

from catalog.models import ExternalResource, IdType, Item, Movie, TVSeason, TVShow
from catalog.models import item as item_module

_URLS = {
    IdType.TMDB_TVSeason: "https://www.themoviedb.org/tv/{}/season/{}",
    IdType.TMDB_TV: "https://www.themoviedb.org/tv/{}",
    IdType.TMDB_Movie: "https://www.themoviedb.org/movie/{}",
}


@pytest.fixture
def counts(monkeypatch) -> list[tuple[str, dict | None]]:
    calls: list[tuple[str, dict | None]] = []

    def _count(key: str, value=1, attributes=None) -> None:
        calls.append((key, attributes))

    monkeypatch.setattr(item_module, "sentry_count", _count)
    return calls


def _link(
    id_type: IdType, id_value: str, lookup_ids: dict, model: type[Item]
) -> ExternalResource:
    res = ExternalResource.objects.create(
        id_type=id_type,
        id_value=id_value,
        url=_URLS[id_type].format(*id_value.split("-")),
        metadata={"title": f"{id_type} {id_value}"},
        other_lookup_ids=lookup_ids,
        scraped_time=timezone.now(),
    )
    res.match_and_link_item(model, False)
    assert res.item is not None
    return res


@pytest.mark.django_db(databases="__all__")
class TestMatchConflict:
    def test_other_season_of_same_show(self, counts):
        s1 = _link(IdType.TMDB_TVSeason, "99388-1", {"imdb": "tt2708580"}, TVSeason)
        s26 = _link(IdType.TMDB_TVSeason, "99388-26", {"imdb": "tt2708580"}, TVSeason)
        assert s26.item != s1.item
        assert ("catalog.match.conflict", {"rule": "tmdb_season"}) in counts

    def test_specials_and_season_one(self, counts):
        s1 = _link(IdType.TMDB_TVSeason, "1639-1", {"imdb": "tt0813715"}, TVSeason)
        s0 = _link(IdType.TMDB_TVSeason, "1639-0", {"imdb": "tt0813715"}, TVSeason)
        assert s0.item != s1.item

    def test_different_imdb_through_shared_wikidata(self, counts):
        eve22 = _link(
            IdType.TMDB_TV,
            "138251",
            {"imdb": "tt20723826", "wikidata": "Q2066628"},
            TVShow,
        )
        eve03 = _link(
            IdType.TMDB_TV,
            "1994",
            {"imdb": "tt0367379", "wikidata": "Q2066628"},
            TVShow,
        )
        assert eve03.item != eve22.item
        assert ("catalog.match.conflict", {"rule": "tmdb_imdb"}) in counts

    def test_tmdb_duplicate_without_imdb_still_merges(self, counts):
        a = _link(
            IdType.TMDB_TV, "308329", {"imdb": "tt14986406", "wikidata": "Q1"}, TVShow
        )
        b = _link(IdType.TMDB_TV, "333251", {"wikidata": "Q1"}, TVShow)
        assert b.item == a.item
        assert not [c for c in counts if c[0] == "catalog.match.conflict"]

    def test_tmdb_duplicate_with_same_imdb_still_merges(self, counts):
        a = _link(IdType.TMDB_Movie, "581859", {"imdb": "tt5109280"}, Movie)
        b = _link(IdType.TMDB_Movie, "1000001", {"imdb": "tt5109280"}, Movie)
        assert b.item == a.item

    def test_refetch_stays_on_split_item(self, counts):
        s1 = _link(IdType.TMDB_TVSeason, "99388-1", {"imdb": "tt2708580"}, TVSeason)
        # primary id is IMDb, so the resource's own id does not pin it (step 1)
        s26 = _link(IdType.TMDB_TVSeason, "99388-26", {"imdb": "tt2900026"}, TVSeason)
        split_item = s26.item
        assert split_item is not None and split_item != s1.item
        s26.other_lookup_ids = {"imdb": "tt2708580"}
        s26.save()
        s26.match_and_link_item(TVSeason, False)
        assert s26.item == split_item
        s26.refresh_from_db()
        assert s26.item_id == split_item.pk
        assert not [c for c in counts if c[0] == "catalog.resource.rematch_moved"]

    def test_rematch_without_conflict_moves(self, counts):
        target = _link(IdType.TMDB_Movie, "200", {"imdb": "tt0000500"}, Movie)
        res = _link(IdType.TMDB_Movie, "100", {"imdb": "tt0000700"}, Movie)
        assert res.item != target.item
        res.other_lookup_ids = {"imdb": "tt0000500"}
        res.save()
        res.match_and_link_item(Movie, False)
        assert res.item == target.item
        moved = [c for c in counts if c[0] == "catalog.resource.rematch_moved"]
        assert moved == [
            ("catalog.resource.rematch_moved", {"site": str(IdType.TMDB_Movie)})
        ]
