"""Duplicate report, reviewed merge and undo for items sharing an external id."""

import csv
import io
import json

import pytest
from django.core.management import call_command

from catalog.dedup import book_format, report, titles_similar
from catalog.models import (
    Album,
    Edition,
    ExternalResource,
    IdType,
    Item,
    Movie,
    TVSeason,
    TVShow,
)
from journal.models import Mark, ShelfMember, ShelfType
from users.models import User

_n = 0


def _item(model, title, year=None, **extra):
    meta = {}
    if year:
        meta["pub_year" if model is Edition else "release_date"] = (
            year if model is Edition else f"{year}-01-01"
        )
    return model.objects.create(
        localized_title=[{"lang": "en", "text": title}], **meta, **extra
    )


def _res(item, id_type, id_value=None, **lookup):
    global _n
    _n += 1
    id_value = id_value or f"v{_n}"
    url = (
        id_value
        if id_type == IdType.Fediverse
        else f"https://example.org/{id_type}/{id_value}"
    )
    return ExternalResource.objects.create(
        item=item,
        id_type=id_type,
        id_value=id_value,
        url=url,
        other_lookup_ids=lookup,
    )


def _peer(item, n, **lookup):
    return _res(item, IdType.Fediverse, f"https://bridge.neodb.net/book/{n}", **lookup)


def _rows() -> list[dict]:
    out = io.StringIO()
    report(out, "https://site.test")
    return list(csv.DictReader(io.StringIO(out.getvalue())))


def _pair(rows, a: Item, b: Item) -> dict | None:
    ids = {str(a.pk), str(b.pk)}
    for r in rows:
        if {r["source_id"], r["target_id"]} == ids:
            return r


def _mark(user, item):
    Mark(user.identity, item).update(ShelfType.COMPLETE, "", 8, [], 0)


def test_titles_similar():
    assert titles_similar(["The Hobbit"], ["the hobbit!"])
    assert titles_similar(["The Hobbit, or There and Back Again"], ["The Hobbit"])
    assert not titles_similar(["The Hobbit"], ["Dune"])


def test_book_format():
    assert book_format("hardcover", None) == "hard"
    assert book_format(None, "Paperback") == "soft"
    assert book_format(None, "精装") == "hard"
    assert book_format(None, "Kindle Edition") == "ebook"
    assert book_format(None, None) is None


@pytest.mark.django_db(databases="__all__")
class TestReport:
    def test_tiers(self):
        # A: a peer-only copy cites the id another item owns
        gr = _item(Edition, "The Hobbit", 2001)
        _res(gr, IdType.Goodreads, "11734")
        copy = _item(Edition, "The Hobbit", 2001)
        _peer(copy, 1, goodreads="11734")
        # B1: two editions cite one ISBN
        d1 = _item(Edition, "Dune", 1990)
        _res(d1, IdType.DoubanBook, isbn="9780000000001")
        d2 = _item(Edition, "Dune", 1990)
        _res(d2, IdType.GoogleBooks, isbn="9780000000001")
        # B3: same ISBN, titles differ
        e1 = _item(Edition, "Emma", 1990)
        _res(e1, IdType.DoubanBook, isbn="9780000000002")
        e2 = _item(Edition, "Persuasion", 1990)
        _res(e2, IdType.GoogleBooks, isbn="9780000000002")
        # B3: editions one year apart
        y1 = _item(Edition, "Ulysses", 1990)
        _res(y1, IdType.Goodreads, "555")
        y2 = _item(Edition, "Ulysses", 1991)
        _res(y2, IdType.DoubanBook, goodreads="555")
        # C: a movie and a show cite one IMDb id
        m = _item(Movie, "Heat", 1995)
        _res(m, IdType.IMDB, "tt0113277")
        s = _item(TVShow, "Heat", 1995)
        _res(s, IdType.TMDB_TV, imdb="tt0113277")
        # excluded: a show and its season share an IMDb id
        show = _item(TVShow, "Lost", 2004)
        _res(show, IdType.IMDB, "tt0411008")
        season = _item(TVSeason, "Lost Season 1", 2004)
        _res(season, IdType.TMDB_TVSeason, imdb="tt0411008")
        # X: only a Wikidata id in common
        a1 = _item(Album, "Blue", 1971)
        _res(a1, IdType.AppleMusic, wikidata="Q1")
        a2 = _item(Album, "Blue", 1971)
        _res(a2, IdType.Spotify_Album, wikidata="Q1")

        rows = _rows()
        r = _pair(rows, gr, copy)
        assert r and r["tier"] == "A"
        assert r["source_id"] == str(copy.pk)
        assert r["target_id"] == str(gr.pk)
        assert r["approve"] == ""
        assert _pair(rows, d1, d2)["tier"] == "B1"  # type: ignore[index]
        r = _pair(rows, e1, e2)
        assert r and r["tier"] == "B3" and "titles differ" in r["reason"]
        r = _pair(rows, y1, y2)
        assert r and r["tier"] == "B3" and "years differ" in r["reason"]
        assert _pair(rows, m, s)["tier"] == "C"  # type: ignore[index]
        assert _pair(rows, show, season) is None
        assert _pair(rows, a1, a2)["tier"] == "X"  # type: ignore[index]

    def test_marked_both_is_b2(self):
        gr = _item(Edition, "The Hobbit", 2001)
        _res(gr, IdType.Goodreads, "11734")
        copy = _item(Edition, "The Hobbit", 2001)
        _peer(copy, 1, goodreads="11734")
        u = User.register(email="both@example.com", username="both")
        _mark(u, gr)
        _mark(u, copy)
        r = _pair(_rows(), gr, copy)
        assert r and r["tier"] == "B2"
        assert r["both_marked"] == "1"

    def test_peer_citation_ignored_on_item_with_own_source(self):
        a = _item(Edition, "The Hobbit", 2001)
        _res(a, IdType.Goodreads, "11734")
        b = _item(Edition, "The Hobbit", 2001)
        _res(b, IdType.DoubanBook)
        _peer(b, 2, goodreads="11734")
        assert _pair(_rows(), a, b) is None

    def test_conflicting_formats_not_tier_a(self):
        gr = _item(Edition, "The Hobbit", 2001, format="hardcover")
        _res(gr, IdType.Goodreads, "11734")
        copy = _item(Edition, "The Hobbit", 2001, format="paperback")
        _peer(copy, 1, goodreads="11734")
        r = _pair(_rows(), gr, copy)
        assert r and r["tier"] == "B3" and "formats differ" in r["reason"]

    def test_large_group_is_skipped(self):
        items = []
        for _ in range(7):
            i = _item(Edition, "Common", 2000)
            _res(i, IdType.DoubanBook, isbn="9780000000009")
            items.append(i)
        assert _pair(_rows(), items[0], items[1]) is None

    def test_cluster_of_three_not_tier_a(self):
        gr = _item(Edition, "The Hobbit", 2001)
        _res(gr, IdType.Goodreads, "11734")
        c1 = _item(Edition, "The Hobbit", 2001)
        _peer(c1, 1, goodreads="11734")
        c2 = _item(Edition, "The Hobbit", 2001)
        _peer(c2, 2, goodreads="11734")
        r = _pair(_rows(), gr, c1)
        assert r and r["tier"] == "B3" and "cluster of 3" in r["reason"]

    def test_command_reports_shared_primary_ids(self, tmp_path):
        a = _item(Movie, "Heat", 1995)
        b = _item(Movie, "Heat", 1995)
        Item.objects.filter(pk__in=[a.pk, b.pk]).update(
            primary_lookup_id_type=IdType.IMDB, primary_lookup_id_value="tt0113277"
        )
        out = io.StringIO()
        call_command("dedup", "report", "-o", str(tmp_path / "r.csv"), stdout=out)
        assert f"Movie imdb:tt0113277 [{a.pk}, {b.pk}]" in out.getvalue()


def _write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


@pytest.mark.django_db(databases="__all__")
class TestMerge:
    def _setup(self, tmp_path):
        self.target = _item(Edition, "The Hobbit", 2001)
        self.target_res = _res(self.target, IdType.Goodreads, "11734")
        self.source = _item(Edition, "The Hobbit", 2001)
        self.source_res = _peer(self.source, 1, goodreads="11734")
        self.d1 = _item(Edition, "Dune", 1990)
        _res(self.d1, IdType.DoubanBook, isbn="9780000000001")
        self.d2 = _item(Edition, "Dune", 1990)
        _res(self.d2, IdType.GoogleBooks, isbn="9780000000001")
        self.user = User.register(email="m@example.com", username="m")
        _mark(self.user, self.source)
        self.old = _item(Edition, "The Hobbit (old)", 2001)
        self.old.merge_to(self.source)
        rows = _rows()
        for r in rows:
            r["approve"] = "y"
        self.csv = tmp_path / "pairs.csv"
        _write(self.csv, rows)
        self.log = tmp_path / "undo.jsonl"

    def test_dry_run_changes_nothing(self, tmp_path):
        self._setup(tmp_path)
        out = io.StringIO()
        call_command("dedup", "merge", "--csv", str(self.csv), stdout=out)
        assert "dry-run: 2" in out.getvalue()
        self.source.refresh_from_db()
        assert self.source.merged_to_item_id is None

    def test_commit_tier_filter_and_undo(self, tmp_path):
        self._setup(tmp_path)
        out = io.StringIO()
        call_command(
            "dedup",
            "merge",
            "--csv",
            str(self.csv),
            "--tier",
            "A",
            "--commit",
            "--log",
            str(self.log),
            stdout=out,
        )
        assert "merged: 1" in out.getvalue()
        self.source.refresh_from_db()
        self.d1.refresh_from_db()
        self.d2.refresh_from_db()
        assert self.source.merged_to_item_id == self.target.pk
        assert self.d1.merged_to_item_id is None
        assert self.d2.merged_to_item_id is None
        self.source_res.refresh_from_db()
        assert self.source_res.item_id == self.target.pk
        assert ShelfMember.objects.filter(item=self.target).count() == 1
        self.old.refresh_from_db()
        assert self.old.merged_to_item_id == self.target.pk
        entry = json.loads(self.log.read_text())
        assert entry["source"] == self.source.pk
        assert entry["resources"] == [self.source_res.pk]
        assert len(entry["pieces"]) >= 1

        call_command("dedup", "undo", "--log", str(self.log), stdout=io.StringIO())
        self.source.refresh_from_db()
        self.source_res.refresh_from_db()
        self.old.refresh_from_db()
        assert self.source.merged_to_item_id is None
        assert self.source_res.item_id == self.source.pk
        assert self.old.merged_to_item_id == self.source.pk
        assert ShelfMember.objects.filter(item=self.source).count() == 1
        assert ShelfMember.objects.filter(item=self.target).count() == 0

    def test_commit_requires_log(self, tmp_path):
        self._setup(tmp_path)
        with pytest.raises(Exception, match="--log"):
            call_command("dedup", "merge", "--csv", str(self.csv), "--commit")

    def test_pieces_are_never_deleted(self, tmp_path):
        self._setup(tmp_path)
        _mark(self.user, self.target)
        call_command(
            "dedup",
            "merge",
            "--csv",
            str(self.csv),
            "--tier",
            "A,B2",
            "--commit",
            "--log",
            str(self.log),
            stdout=io.StringIO(),
        )
        self.source.refresh_from_db()
        assert self.source.merged_to_item_id == self.target.pk
        # the clashing mark stays on the merged item, not deleted
        assert ShelfMember.objects.filter(item=self.source).count() == 1
        assert ShelfMember.objects.filter(item=self.target).count() == 1

    def test_live_recheck(self, tmp_path):
        self._setup(tmp_path)
        self.source_res.other_lookup_ids = {}
        self.source_res.save()
        Item.objects.filter(pk__in=[self.d1.pk, self.d2.pk]).update(is_protected=True)
        out = io.StringIO()
        call_command(
            "dedup",
            "merge",
            "--csv",
            str(self.csv),
            "--commit",
            "--log",
            str(self.log),
            stdout=out,
        )
        assert "evidence gone: 1" in out.getvalue()
        assert "source protected: 1" in out.getvalue()
