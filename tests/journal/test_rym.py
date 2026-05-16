import csv
import io

import pytest

from catalog.models import Album
from journal.importers.rym import (
    RymImporter,
    _bbcode_to_md,
    _row_artist,
    update_row_in_matched_file,
)
from journal.models import Mark, ShelfType
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestBBCodeConversion:
    def test_bold_italic_strike(self):
        assert _bbcode_to_md("[b]hi[/b]") == "**hi**"
        assert _bbcode_to_md("[i]oh[/i]") == "*oh*"
        assert _bbcode_to_md("[s]gone[/s]") == "~~gone~~"

    def test_underline_dropped(self):
        assert _bbcode_to_md("[u]link[/u]") == "link"

    def test_url(self):
        assert _bbcode_to_md("[url=https://x.com]X[/url]") == "[X](https://x.com)"
        assert _bbcode_to_md("[url]https://y.com[/url]") == "https://y.com"

    def test_ref_tag_stripped(self):
        assert _bbcode_to_md("see [Artist123]") == "see"
        assert _bbcode_to_md("[Album999]done[/Album999]") == "done"

    def test_newlines_preserved(self):
        out = _bbcode_to_md("[b]a[/b]\n\n[i]b[/i]")
        assert out == "**a**\n\n*b*"

    def test_combined(self):
        src = "[b]BB[/b]\n\n[i]III[/i]\n\n[Artist577519]"
        assert _bbcode_to_md(src) == "**BB**\n\n*III*"


@pytest.mark.django_db(databases="__all__")
class TestRowArtist:
    def test_localized_takes_precedence(self):
        assert (
            _row_artist(
                {
                    "First Name": "John",
                    "Last Name": "Doe",
                    "First Name localized": "山田",
                    "Last Name localized": "太郎",
                }
            )
            == "山田 太郎"
        )

    def test_fallback_to_ascii(self):
        assert (
            _row_artist({"First Name": "Action", "Last Name": "Bronson"})
            == "Action Bronson"
        )


@pytest.mark.django_db(databases="__all__")
class TestValidateFile:
    def test_accepts_rym_header(self):
        assert RymImporter.validate_file(
            io.BytesIO(b"RYM Album, First Name,Last Name,Title\n1,2,3,4\n")
        )

    def test_rejects_other_csv(self):
        assert not RymImporter.validate_file(
            io.BytesIO(b"Book Id,Title,Author\n1,2,3\n")
        )

    def test_accepts_bom(self):
        assert RymImporter.validate_file(
            io.BytesIO("﻿RYM Album, First Name\n".encode("utf-8"))
        )


@pytest.mark.django_db(databases="__all__")
class TestMatchedFileGeneration:
    """Phase 1: drive _run_matching() with patched matchers and inspect the CSV it writes."""

    @pytest.fixture(autouse=True)
    def setup_data(self, tmp_path):
        self.user = User.register(email="rymtest@example.com", username="rymtestuser")
        self.identity = self.user.identity
        self.in_path = str(tmp_path / "rym_input.csv")
        with open(self.in_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "RYM Album",
                    " First Name",
                    "Last Name",
                    "First Name localized",
                    " Last Name localized",
                    "Title",
                    "Release_Date",
                    "Rating",
                    "Ownership",
                    "Purchase Date",
                    "Media Type",
                    " Review",
                    " Review Title",
                ]
            )
            w.writerow(
                [
                    "10",
                    "The Jimi",
                    "Hendrix Experience",
                    "",
                    "",
                    "Are You Experienced?",
                    "1967",
                    "10",
                    "o",
                    "2026-04-01",
                    "CD",
                    "[b]bold[/b] review",
                    "",
                ]
            )
            w.writerow(
                [
                    "20",
                    "",
                    "Radiohead",
                    "",
                    "",
                    "OK Computer",
                    "1997",
                    "9",
                    "n",
                    "",
                    "",
                    "",
                    "",
                ]
            )
            w.writerow(
                [
                    "30",
                    "Action",
                    "Bronson",
                    "",
                    "",
                    "Planet Frog",
                    "2026",
                    "0",
                    "w",
                    "",
                    "MP3",
                    "",
                    "",
                ]
            )
            w.writerow(
                [
                    "50",
                    "The",
                    "Vehicle Birth",
                    "",
                    "",
                    "Tragedy",
                    "",
                    "0",
                    "u",
                    "",
                    "",
                    "",
                    "",
                ]
            )

    def _make_task(self, **extra):
        return RymImporter.create(
            self.user,
            phase="matching",
            visibility=0,
            file=self.in_path,
            **extra,
        )

    def _stub_matchers(self, monkeypatch, ext_url=None):
        monkeypatch.setattr(RymImporter, "_local_match", lambda self, *a, **k: None)
        monkeypatch.setattr(
            RymImporter, "_external_match", lambda self, *a, **k: ext_url
        )

    def test_default_shelf_and_collect_date(self, monkeypatch):
        self._stub_matchers(monkeypatch)
        task = self._make_task()
        task.run()
        path = task.metadata["matched_file"]
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        # Ownership=o -> COMPLETE; collect_date copied from Purchase Date
        assert rows[0]["shelf"] == ShelfType.COMPLETE.value
        assert rows[0]["collect_date"] == "2026-04-01"
        # Ownership=n with rating 9 -> COMPLETE (rating implies you heard it)
        assert rows[1]["shelf"] == ShelfType.COMPLETE.value
        # Ownership=w -> WISHLIST
        assert rows[2]["shelf"] == ShelfType.WISHLIST.value
        # Ownership=u with rating 0 and no review -> shelf left empty (skip);
        # user can override per-row in the preview UI.
        assert rows[3]["shelf"] == ""

    def test_collect_date_default_one_week_ago(self, monkeypatch):
        self._stub_matchers(monkeypatch)
        task = self._make_task()
        task.run()
        path = task.metadata["matched_file"]
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        # Radiohead row had no Purchase Date -> collect_date defaults to ~today-7d
        assert rows[1]["collect_date"]  # populated
        assert rows[2]["collect_date"]  # populated

    def test_external_match_recorded(self, monkeypatch):
        self._stub_matchers(
            monkeypatch,
            ext_url=("https://musicbrainz.org/release-group/abc-123", "musicbrainz"),
        )
        task = self._make_task()
        task.run()
        path = task.metadata["matched_file"]
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert all(r["match_source"] == "musicbrainz" for r in rows)
        assert all(r["link"].startswith("https://musicbrainz.org/") for r in rows)

    def test_phase_transitions_to_preview(self, monkeypatch):
        self._stub_matchers(monkeypatch)
        task = self._make_task()
        task.run()
        assert task.metadata["phase"] == "preview"
        assert task.metadata["total"] == 4
        assert task.metadata["processed"] == 4


@pytest.mark.django_db(databases="__all__")
class TestImportPhase:
    @pytest.fixture(autouse=True)
    def setup_data(self, tmp_path):
        self.user = User.register(email="rymimp@example.com", username="rymimpuser")
        self.identity = self.user.identity
        # seed a local Album for direct link resolution
        self.album_hendrix = Album.objects.create(
            title="Are You Experienced?",
            localized_title=[{"lang": "en", "text": "Are You Experienced?"}],
            primary_lookup_id_type="dummy",
            primary_lookup_id_value="rymtest-hendrix",
        )
        self.album_hendrix.artist = ["The Jimi Hendrix Experience"]
        self.album_hendrix.save()
        self.album_bronson = Album.objects.create(
            title="Planet Frog",
            localized_title=[{"lang": "en", "text": "Planet Frog"}],
            primary_lookup_id_type="dummy",
            primary_lookup_id_value="rymtest-bronson",
        )
        self.album_bronson.artist = ["Action Bronson"]
        self.album_bronson.save()
        self.matched_path = str(tmp_path / "matched.csv")
        with open(self.matched_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            headers = [
                "RYM Album",
                "First Name",
                "Last Name",
                "First Name localized",
                "Last Name localized",
                "Title",
                "Release_Date",
                "Rating",
                "Ownership",
                "Purchase Date",
                "Media Type",
                "Review",
                "Review Title",
                "link",
                "match_source",
                "shelf",
                "collect_date",
                "notes",
            ]
            w.writerow(headers)
            w.writerow(
                [
                    "10",
                    "The Jimi",
                    "Hendrix Experience",
                    "",
                    "",
                    "Are You Experienced?",
                    "1967",
                    "10",
                    "o",
                    "",
                    "CD",
                    "[b]bold[/b] short review",
                    "",
                    self.album_hendrix.url,
                    "local",
                    ShelfType.COMPLETE.value,
                    "2026-04-01",
                    "",
                ]
            )
            w.writerow(
                [
                    "30",
                    "Action",
                    "Bronson",
                    "",
                    "",
                    "Planet Frog",
                    "2026",
                    "0",
                    "w",
                    "",
                    "MP3",
                    "",
                    "",
                    self.album_bronson.url,
                    "local",
                    ShelfType.WISHLIST.value,
                    "2026-05-01",
                    "",
                ]
            )
            # row 3: unmatched (no link) -> skipped
            w.writerow(
                [
                    "50",
                    "The",
                    "Vehicle Birth",
                    "",
                    "",
                    "Tragedy",
                    "",
                    "0",
                    "u",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "none",
                    "",
                    "",
                    "",
                ]
            )

    def _run_import_task(self):
        task = RymImporter.create(
            self.user,
            phase="importing",
            visibility=0,
            file="dummy",
            matched_file=self.matched_path,
        )
        task.run()
        return task

    def test_import_creates_complete_mark_with_short_comment(self):
        task = self._run_import_task()
        assert task.metadata["imported"] == 2
        assert task.metadata["skipped"] == 1
        assert task.metadata["failed"] == 0
        mark = Mark(self.identity, self.album_hendrix)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 10
        # short BBCode -> markdown in comment_text
        assert mark.comment_text and "**bold**" in mark.comment_text

    def test_import_creates_wishlist_no_rating(self):
        self._run_import_task()
        mark = Mark(self.identity, self.album_bronson)
        assert mark.shelf_type == ShelfType.WISHLIST
        assert mark.rating_grade is None

    def test_import_phase_completes(self):
        task = self._run_import_task()
        assert task.metadata["phase"] == "done"


@pytest.mark.django_db(databases="__all__")
class TestUpdateRowInMatchedFile:
    def test_atomic_rewrite(self, tmp_path):
        path = str(tmp_path / "matched.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Title", "link", "shelf", "collect_date"])
            w.writerow(["Album A", "", "", ""])
            w.writerow(["Album B", "https://x", "complete", "2026-05-01"])
        row = update_row_in_matched_file(
            path,
            0,
            {"link": "/album/abc", "shelf": "wishlist", "collect_date": "2026-05-10"},
        )
        assert row is not None
        assert row["link"] == "/album/abc"
        assert row["shelf"] == "wishlist"
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["link"] == "/album/abc"
        assert rows[1]["link"] == "https://x"

    def test_out_of_range_returns_none(self, tmp_path):
        path = str(tmp_path / "matched.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Title", "link", "shelf", "collect_date"])
            w.writerow(["Album A", "", "", ""])
        assert update_row_in_matched_file(path, 5, {"link": "x"}) is None


@pytest.mark.django_db(databases="__all__")
class TestExternalSearchFieldQueries:
    def test_musicbrainz_lucene_query(self):
        from catalog.sites.musicbrainz import MusicBrainzRelease

        q = MusicBrainzRelease.build_field_query("OK Computer", "Radiohead", "1997")
        assert 'release:"OK Computer"' in q
        assert 'artist:"Radiohead"' in q
        assert "date:1997" in q
        assert " AND " in q

    def test_musicbrainz_escapes_special_chars(self):
        from catalog.sites.musicbrainz import MusicBrainzRelease

        q = MusicBrainzRelease.build_field_query('A:B"', "X", None)
        # special chars escaped with backslash
        assert "\\:" in q
        assert '\\"' in q

    def test_spotify_field_query(self):
        from catalog.sites.spotify import Spotify

        q = Spotify.build_field_query("OK Computer", "Radiohead", "1997")
        assert 'album:"OK Computer"' in q
        assert 'artist:"Radiohead"' in q
        assert "year:1997" in q

    def test_spotify_field_query_no_year(self):
        from catalog.sites.spotify import Spotify

        q = Spotify.build_field_query("X", "Y", None)
        assert "year:" not in q

    def test_spotify_field_query_invalid_year(self):
        from catalog.sites.spotify import Spotify

        q = Spotify.build_field_query("X", "Y", "abcd")
        assert "year:" not in q
