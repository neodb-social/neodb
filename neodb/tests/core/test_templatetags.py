from datetime import datetime, timedelta

from django.utils.safestring import SafeString

from common.templatetags.duration import (
    code_to_lang,
    duration_format,
    make_range,
    naturaldelta,
    rating_star,
    relative_uri,
)
from common.templatetags.highlight import highlight
from common.templatetags.strip_scheme import strip_scheme
from common.templatetags.truncate import truncate


class TestDurationFormat:
    def test_minutes_and_seconds(self):
        # 125 seconds with unit=1 -> 2:05
        assert duration_format("125", "1") == "02:05"

    def test_hours_minutes_seconds(self):
        # 3661 seconds -> 1:01:01
        assert duration_format("3661", "1") == "1:01:01"

    def test_with_unit_conversion(self):
        # 7200 milliseconds with unit=1000 -> 7 seconds -> 00:07
        assert duration_format("7200", "1000") == "00:07"

    def test_zero_duration(self):
        assert duration_format("0", "1") == "00:00"

    def test_none_value(self):
        # @stringfilter converts None to "None", which triggers format error
        result = duration_format(None, "1")
        assert "format error" in result

    def test_empty_string(self):
        assert duration_format("", "1") == "00:00"

    def test_invalid_value(self):
        result = duration_format("abc", "1")
        assert "format error" in result

    def test_zero_unit(self):
        # division by zero should be caught
        result = duration_format("100", "0")
        assert "format error" in result

    def test_large_value(self):
        # 86400 seconds = 24 hours
        assert duration_format("86400", "1") == "24:00:00"


class TestNaturaldelta:
    def test_none(self):
        assert naturaldelta(None) == ""

    def test_just_now(self):
        v = datetime.now() - timedelta(seconds=30)
        assert naturaldelta(v) != ""  # returns translated "just now"

    def test_minutes(self):
        v = datetime.now() - timedelta(minutes=5)
        assert naturaldelta(v) == "5m"

    def test_hours(self):
        v = datetime.now() - timedelta(hours=3)
        assert naturaldelta(v) == "3h"

    def test_days(self):
        v = datetime.now() - timedelta(days=5)
        assert naturaldelta(v) == "5d"

    def test_weeks(self):
        v = datetime.now() - timedelta(weeks=3)
        assert naturaldelta(v) == "3w"

    def test_months(self):
        v = datetime.now() - timedelta(days=90)
        assert naturaldelta(v) == "3mo"

    def test_years(self):
        v = datetime.now() - timedelta(days=730)
        assert naturaldelta(v) == "2yr"


class TestRatingStar:
    def test_normal_rating(self):
        result = rating_star("7.5")
        assert isinstance(result, SafeString)
        assert "width:75%" in result
        assert 'data-rating="7.5"' in result

    def test_zero_rating(self):
        result = rating_star("0")
        assert "width:0%" in result

    def test_max_rating(self):
        result = rating_star("10")
        assert "width:100%" in result

    def test_above_max_clamped(self):
        result = rating_star("15")
        assert "width:100%" in result

    def test_negative_clamped(self):
        result = rating_star("-5")
        assert "width:0%" in result

    def test_none_value(self):
        result = rating_star(None)
        assert "width:0%" in result

    def test_invalid_string(self):
        result = rating_star("abc")
        assert "width:0%" in result

    def test_decimal_rounding(self):
        result = rating_star("3.33")
        # round(10 * 3.33) = round(33.3) = 33
        assert "width:33%" in result


class TestRelativeUri:
    def test_strips_site_url(self, settings):
        settings.SITE_INFO = {"site_url": "https://example.org"}
        assert relative_uri("https://example.org/path/to/page") == "/path/to/page"

    def test_no_site_url_prefix(self, settings):
        settings.SITE_INFO = {"site_url": "https://example.org"}
        assert relative_uri("https://other.com/page") == "https://other.com/page"

    def test_empty_value(self, settings):
        settings.SITE_INFO = {"site_url": "https://example.org"}
        assert relative_uri("") == ""


class TestMakeRange:
    def test_normal(self):
        assert list(make_range(3)) == [1, 2, 3]

    def test_one(self):
        assert list(make_range(1)) == [1]

    def test_zero(self):
        assert list(make_range(0)) == []


class TestCodeToLang:
    def test_known_language_code(self):
        # "en" should be in LANGUAGE_CODES
        result = code_to_lang("en")
        assert result  # should return a non-empty string

    def test_unknown_code_returns_itself(self):
        assert code_to_lang("zzz_unknown") == "zzz_unknown"

    def test_empty_code(self):
        assert code_to_lang("") == ""


class TestHighlight:
    def test_single_word(self):
        result = highlight("hello world", "hello")
        assert "<mark>hello</mark>" in result
        assert isinstance(result, SafeString)

    def test_case_insensitive(self):
        result = highlight("Hello World", "hello")
        assert "<mark>Hello</mark>" in result

    def test_multiple_words(self):
        result = highlight("foo bar baz", "foo baz")
        assert "<mark>foo</mark>" in result
        assert "<mark>baz</mark>" in result

    def test_no_match(self):
        result = highlight("hello world", "xyz")
        assert "<mark>" not in result
        assert "hello world" in result

    def test_empty_search(self):
        result = highlight("hello", "")
        assert result == "hello"

    def test_html_escaping(self):
        result = highlight("<script>alert(1)</script>", "script")
        assert "<script>" not in result
        assert "&lt;" in result

    def test_overlapping_words_longest_first(self):
        result = highlight("testing", "test testing")
        # "testing" is longer and should match first
        assert "<mark>testing</mark>" in result


class TestStripScheme:
    def test_https(self):
        assert strip_scheme("https://example.com/page") == "example.com/page"

    def test_http(self):
        assert strip_scheme("http://example.com/page") == "example.com/page"

    def test_trailing_slash(self):
        assert strip_scheme("https://example.com/") == "example.com"

    def test_no_scheme(self):
        assert strip_scheme("example.com/page") == "example.com/page"

    def test_empty_string(self):
        assert strip_scheme("") == ""


class TestTruncate:
    def test_short_string_unchanged(self):
        assert truncate("hello", "10") == "hello"

    def test_long_string_truncated(self):
        result = truncate("hello world this is a long string", "10")
        assert len(result) <= 13  # 10 chars + "..."
        assert result.endswith("...")

    def test_invalid_length_returns_original(self):
        assert truncate("hello", "abc") == "hello"

    def test_exact_length(self):
        result = truncate("hello", "5")
        assert result == "hello"
