import pytest

from catalog.models import (
    Album,
    Edition,
    Game,
    Movie,
    Podcast,
    TVEpisode,
    TVSeason,
    TVShow,
)
from journal.models import Note


class TestNoteProgressDisplay:
    def test_empty_progress_value(self):
        note = Note()
        note.progress_value = None
        assert note.progress_display == ""

    def test_progress_value_without_type(self):
        note = Note()
        note.progress_value = "42"
        note.progress_type = None
        assert note.progress_display == "42"

    def test_progress_value_with_unknown_type(self):
        note = Note()
        note.progress_value = "42"
        note.progress_type = "unknown_type"
        assert note.progress_display == "42"

    def test_progress_value_with_percentage_type(self):
        note = Note()
        note.progress_value = "50"
        note.progress_type = Note.ProgressType.PERCENTAGE
        assert note.progress_display == "50%"

    def test_progress_value_with_timestamp_type(self):
        note = Note()
        note.progress_value = "1:23:45"
        note.progress_type = Note.ProgressType.TIMESTAMP
        assert note.progress_display == "1:23:45"

    def test_progress_value_non_numeric_with_page(self):
        note = Note()
        note.progress_value = "chapter-one"
        note.progress_type = Note.ProgressType.PAGE
        # non-numeric values get label prefix instead of template
        assert "Page" in note.progress_display
        assert "chapter-one" in note.progress_display


class TestNoteExtractProgress:
    def test_track_prefix(self):
        typ, val = Note.extract_progress("trk 5")
        assert typ == Note.ProgressType.TRACK
        assert val == "5"

    def test_track_full_prefix(self):
        typ, val = Note.extract_progress("track 3")
        assert typ == Note.ProgressType.TRACK
        assert val == "3"

    def test_cycle_prefix(self):
        typ, val = Note.extract_progress("cycle 2")
        assert typ == Note.ProgressType.CYCLE
        assert val == "2"

    def test_percentage_with_postfix(self):
        typ, val = Note.extract_progress("50%")
        assert typ == Note.ProgressType.PERCENTAGE
        assert val == "50"

    def test_timestamp_with_colon(self):
        typ, val = Note.extract_progress("1:23:45")
        assert typ == "timestamp"
        assert val == "1:23:45"

    def test_number_only_no_type(self):
        typ, val = Note.extract_progress("42")
        assert typ is None
        assert val == "42"

    def test_dash_value(self):
        typ, val = Note.extract_progress("-")
        assert typ is None
        assert val == ""

    def test_no_match(self):
        typ, val = Note.extract_progress("just some text without numbers")
        assert typ is None
        assert val is None


@pytest.mark.django_db(databases="__all__")
class TestNoteGetProgressTypesByItem:
    def test_edition_progress_types(self):
        book = Edition.objects.create(title="Test Book")
        types = Note.get_progress_types_by_item(book)
        assert Note.ProgressType.PAGE in types
        assert Note.ProgressType.CHAPTER in types
        assert Note.ProgressType.PERCENTAGE in types

    def test_movie_progress_types(self):
        movie = Movie.objects.create(title="Test Movie")
        types = Note.get_progress_types_by_item(movie)
        assert Note.ProgressType.PART in types
        assert Note.ProgressType.TIMESTAMP in types
        assert Note.ProgressType.PERCENTAGE in types

    def test_tvshow_progress_types(self):
        show = TVShow.objects.create(title="Test Show")
        types = Note.get_progress_types_by_item(show)
        assert Note.ProgressType.EPISODE in types
        assert Note.ProgressType.PART in types

    def test_tvseason_progress_types(self):
        season = TVSeason.objects.create(title="Test Season")
        types = Note.get_progress_types_by_item(season)
        assert Note.ProgressType.EPISODE in types

    def test_album_progress_types(self):
        album = Album.objects.create(title="Test Album")
        types = Note.get_progress_types_by_item(album)
        assert Note.ProgressType.TRACK in types
        assert Note.ProgressType.TIMESTAMP in types

    def test_game_progress_types(self):
        game = Game.objects.create(title="Test Game")
        types = Note.get_progress_types_by_item(game)
        assert Note.ProgressType.CYCLE in types

    def test_podcast_progress_types(self):
        podcast = Podcast.objects.create(title="Test Podcast")
        types = Note.get_progress_types_by_item(podcast)
        assert Note.ProgressType.EPISODE in types

    def test_tvepisode_returns_empty(self):
        ep = TVEpisode.objects.create(title="Test Episode")
        types = Note.get_progress_types_by_item(ep)
        assert types == []
