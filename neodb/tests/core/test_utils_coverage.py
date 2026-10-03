import uuid

import pytest
from django.http import Http404, QueryDict
from django.template.loader import render_to_string

from common.utils import (
    GenerateDateUUIDMediaFilePath,
    PageLinksGenerator,
    get_uuid_or_404,
)


class TestPageLinksGenerator:
    def test_single_page(self):
        pg = PageLinksGenerator(1, 1)
        assert pg.current_page == 1
        assert pg.has_prev is False
        assert pg.has_next is False
        assert pg.previous_page is None
        assert pg.next_page is None
        assert pg.page_range is not None
        assert list(pg.page_range) == [1]

    def test_first_page_of_many(self):
        pg = PageLinksGenerator(1, 20)
        assert pg.current_page == 1
        assert pg.has_prev is False
        assert pg.has_next is True
        assert pg.previous_page is None
        assert pg.next_page == 2

    def test_last_page_of_many(self):
        pg = PageLinksGenerator(20, 20)
        assert pg.current_page == 20
        assert pg.has_prev is True
        assert pg.has_next is False
        assert pg.previous_page == 19
        assert pg.next_page is None

    def test_middle_page(self):
        pg = PageLinksGenerator(10, 20)
        assert pg.current_page == 10
        assert pg.has_prev is True
        assert pg.has_next is True
        assert pg.previous_page == 9
        assert pg.next_page == 11

    def test_both_sides_overflow(self):
        # total pages less than length
        pg = PageLinksGenerator(2, 3)
        assert pg.start_page == 1
        assert pg.end_page == 3
        assert pg.has_prev is False
        assert pg.has_next is False

    def test_left_side_overflow(self):
        # near the start
        pg = PageLinksGenerator(2, 50)
        assert pg.start_page == 1
        assert pg.has_prev is False
        assert pg.has_next is True

    def test_right_side_overflow(self):
        # near the end
        pg = PageLinksGenerator(49, 50)
        assert pg.end_page == 50
        assert pg.has_next is False
        assert pg.has_prev is True

    def test_query_string_included(self):
        q = QueryDict(mutable=True)
        q["q"] = "search"
        q["page"] = "2"
        pg = PageLinksGenerator(2, 10, query=q)
        assert "q=search" in pg.query_string
        assert "page" not in pg.query_string

    def test_gaps_next_to_first_and_last_page(self):
        assert PageLinksGenerator(10, 20).gap_before is True
        assert PageLinksGenerator(10, 20).gap_after is True
        assert PageLinksGenerator(4, 20).gap_before is False
        assert PageLinksGenerator(17, 20).gap_after is False
        assert PageLinksGenerator(2, 3).gap_before is False
        assert PageLinksGenerator(2, 3).gap_after is False

    def test_query_string_empty(self):
        pg = PageLinksGenerator(1, 5)
        assert pg.query_string == ""

    def test_page_range_covers_correctly(self):
        pg = PageLinksGenerator(5, 10)
        assert pg.page_range is not None
        pages = list(pg.page_range)
        assert pg.current_page in pages
        for p in pages:
            assert 1 <= p <= 10


def _render_pagination(current: int, total: int, query: QueryDict | None = None) -> str:
    return render_to_string(
        "_pagination.html", {"pagination": PageLinksGenerator(current, total, query)}
    )


class TestPaginationTemplate:
    def test_single_page_renders_nothing(self):
        assert _render_pagination(1, 1).strip() == ""

    def test_no_pagination_renders_nothing(self):
        assert render_to_string("_pagination.html", {}).strip() == ""

    def test_previous_shown_while_window_touches_first_page(self):
        html = _render_pagination(2, 20)
        assert 'rel="prev"' in html
        assert html.count('href="?page=1"') == 2
        assert 'rel="next"' in html

    def test_short_list_has_steps(self):
        html = _render_pagination(3, 4)
        assert 'rel="prev"' in html
        assert 'rel="next"' in html

    def test_ends_disable_steps(self):
        first = _render_pagination(1, 20)
        assert 'rel="prev"' not in first
        assert first.count('aria-disabled="true"') == 1
        last = _render_pagination(20, 20)
        assert 'rel="next"' not in last
        assert last.count('aria-disabled="true"') == 1

    def test_current_page_is_not_a_link(self):
        html = _render_pagination(10, 20)
        assert '<span class="num" aria-current="page">10</span>' in html
        assert 'href="?page=10"' not in html

    def test_first_and_last_pages_with_gaps(self):
        html = _render_pagination(10, 20)
        assert 'href="?page=1" class="num">1</a>' in html
        assert ">20</a>" in html
        assert html.count("…") == 2

    def test_no_gap_next_to_first_page(self):
        html = _render_pagination(4, 20)
        assert 'href="?page=1" class="num">1</a>' in html
        assert 'href="?page=2" class="num">2</a>' in html
        assert html.count("…") == 1

    def test_query_string_kept(self):
        q = QueryDict(mutable=True)
        q["q"] = "dune"
        q["page"] = "3"
        html = _render_pagination(3, 20, q)
        assert 'href="?q=dune&amp;page=4"' in html


class TestGenerateDateUUIDMediaFilePath:
    def test_with_trailing_slash(self):
        path = GenerateDateUUIDMediaFilePath("photo.jpg", "uploads/")
        assert path.startswith("uploads/")
        assert path.endswith(".jpg")
        assert "/" in path

    def test_without_trailing_slash(self):
        path = GenerateDateUUIDMediaFilePath("photo.jpg", "uploads")
        assert path.startswith("uploads/")
        assert path.endswith(".jpg")

    def test_preserves_extension(self):
        path = GenerateDateUUIDMediaFilePath("image.webp", "media/")
        assert path.endswith(".webp")


class TestGetUuidOr404:
    def test_valid_b62(self):
        # Create a UUID and encode it
        from django.core.signing import b62_encode

        u = uuid.uuid4()
        b62 = b62_encode(u.int).zfill(22)
        result = get_uuid_or_404(b62)
        assert result == u

    def test_invalid_b62_raises_404(self):
        with pytest.raises(Http404):
            get_uuid_or_404("!!invalid!!")
