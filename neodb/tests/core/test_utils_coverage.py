import uuid

import pytest
from django.http import Http404, QueryDict

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

    def test_contains_date_path(self):
        path = GenerateDateUUIDMediaFilePath("file.png", "media/")
        parts = path.split("/")
        # should have media, year, month, day, filename
        assert len(parts) >= 4


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
