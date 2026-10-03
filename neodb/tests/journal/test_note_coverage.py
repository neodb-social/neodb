from typing import Any, cast

import pytest

from catalog.models import (
    Album,
    Edition,
    Game,
    Item,
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

    def test_short_progress_display(self):
        assert Note.format_progress_short(Note.ProgressType.PAGE, "22") == "p22"
        assert Note.format_progress_short(Note.ProgressType.CHAPTER, "7") == "ch7"
        assert Note.format_progress_short(Note.ProgressType.PERCENTAGE, "50") == "50%"
        assert Note.format_progress_short(None, "custom") == "custom"
        assert Note.format_progress_short("unknown", "42") == "42"


class TestNoteProgressPercentage:
    def test_percentage_type_used_directly(self):
        assert Note.get_progress_percentage(Note.ProgressType.PERCENTAGE, "50") == 50

    def test_percentage_type_ignores_total(self):
        assert (
            Note.get_progress_percentage(Note.ProgressType.PERCENTAGE, "30", 400) == 30
        )

    def test_percentage_clamped_to_max(self):
        assert Note.get_progress_percentage(Note.ProgressType.PERCENTAGE, "150") == 100

    def test_page_type_converted_with_total(self):
        assert Note.get_progress_percentage(Note.ProgressType.PAGE, "100", 400) == 25

    def test_page_type_rounds(self):
        assert Note.get_progress_percentage(Note.ProgressType.PAGE, "1", 3) == 33

    def test_page_type_without_usable_total(self):
        assert Note.get_progress_percentage(Note.ProgressType.PAGE, "100") is None
        assert Note.get_progress_percentage(Note.ProgressType.PAGE, "100", 0) is None
        assert Note.get_progress_percentage(Note.ProgressType.PAGE, "100", -10) is None

    def test_page_type_non_numeric_total(self):
        # Edition.pages comes from item metadata JSON and may arrive as a
        # non-int; a total that cannot be parsed must not raise.
        assert (
            Note.get_progress_percentage(
                Note.ProgressType.PAGE, "100", cast(Any, "many")
            )
            is None
        )
        # A numeric string total is still usable.
        assert (
            Note.get_progress_percentage(
                Note.ProgressType.PAGE, "100", cast(Any, "400")
            )
            == 25
        )

    def test_chapter_type_has_no_percentage(self):
        assert Note.get_progress_percentage(Note.ProgressType.CHAPTER, "3", 10) is None

    def test_empty_or_non_numeric_value(self):
        assert Note.get_progress_percentage(Note.ProgressType.PERCENTAGE, None) is None
        assert Note.get_progress_percentage(Note.ProgressType.PERCENTAGE, "") is None
        # passes the numeric-ish guard but is not a real number -> no crash
        assert (
            Note.get_progress_percentage(Note.ProgressType.PERCENTAGE, "12:30") is None
        )
        assert (
            Note.get_progress_percentage(Note.ProgressType.PAGE, "12-15", 100) is None
        )


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


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            Edition,
            [
                Note.ProgressType.PAGE,
                Note.ProgressType.CHAPTER,
                Note.ProgressType.PERCENTAGE,
            ],
        ),
        (
            Movie,
            [
                Note.ProgressType.PART,
                Note.ProgressType.TIMESTAMP,
                Note.ProgressType.PERCENTAGE,
            ],
        ),
        (
            TVShow,
            [
                Note.ProgressType.PART,
                Note.ProgressType.EPISODE,
                Note.ProgressType.PERCENTAGE,
            ],
        ),
        (
            TVSeason,
            [
                Note.ProgressType.PART,
                Note.ProgressType.EPISODE,
                Note.ProgressType.PERCENTAGE,
            ],
        ),
        (
            Album,
            [
                Note.ProgressType.TRACK,
                Note.ProgressType.TIMESTAMP,
                Note.ProgressType.PERCENTAGE,
            ],
        ),
        (Game, [Note.ProgressType.CYCLE]),
        (Podcast, [Note.ProgressType.EPISODE]),
        (TVEpisode, []),
    ],
)
def test_progress_types_by_item(
    model: type[Item], expected: list[Note.ProgressType]
) -> None:
    assert Note.get_progress_types_by_item(model()) == expected
