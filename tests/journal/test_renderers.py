import pytest

from catalog.models import Edition
from journal.models.renderers import (
    _linkify,
    _normalize_image_src,
    convert_leading_space_in_md,
    has_spoiler,
    html_to_text,
    render_post_with_macro,
    render_rating,
    render_spoiler_text,
    render_text,
    render_title_as_hashtag,
    sanitize_md_images,
)


def _link(url: str) -> str:
    """Helper to build expected anchor tag for a URL."""
    return f'<a href="{url}" rel="nofollow" target="_blank">{url}</a>'


class TestLinkify:
    def test_plain_text_no_urls(self):
        assert _linkify("just some text") == "just some text"

    def test_single_url(self):
        assert _linkify("visit https://example.com today") == (
            f"visit {_link('https://example.com')} today"
        )

    def test_url_with_path(self):
        url = "https://eggplant.place/movie/7DWrhJ7Mz"
        assert _linkify(f"see {url}") == f"see {_link(url)}"

    def test_multiple_urls(self):
        result = _linkify("see https://a.com and https://b.com/path")
        assert _link("https://a.com") in result
        assert _link("https://b.com/path") in result

    def test_url_with_query_params(self):
        result = _linkify("visit https://example.com/path?q=1&x=2")
        assert 'href="https://example.com/path?q=1&amp;x=2"' in result

    def test_escapes_html_in_text(self):
        result = _linkify("a < b > c")
        assert "&lt;" in result
        assert "&gt;" in result

    def test_escapes_script_tags(self):
        result = _linkify("<script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_http_url(self):
        assert _link("http://example.com") in _linkify("visit http://example.com")

    def test_url_with_port(self):
        url = "https://example.com:8080/path"
        assert _link(url) in _linkify(f"at {url}")

    def test_url_in_parentheses(self):
        assert _linkify("(https://example.com)") == f"({_link('https://example.com')})"

    def test_empty_string(self):
        assert _linkify("") == ""


class TestRenderText:
    def test_plain_text(self):
        assert render_text("hello world") == "hello world"

    def test_url_linkified(self):
        result = render_text("visit https://example.com for info")
        assert _link("https://example.com") in result

    def test_newlines_to_br(self):
        result = render_text("line 1\nline 2")
        assert "<br>" in result

    def test_url_across_newlines(self):
        result = render_text("before\nhttps://example.com\nafter")
        assert _link("https://example.com") in result
        assert "<br>" in result

    def test_spoiler_without_url(self):
        result = render_text("check >!secret!< here")
        assert '<span class="spoiler"' in result
        assert "secret" in result

    def test_url_inside_spoiler(self):
        result = render_text("check >!https://secret.com!< here")
        assert '<span class="spoiler"' in result
        assert _link("https://secret.com") in result

    def test_url_outside_spoiler(self):
        result = render_text("see https://public.com and >!hidden!<")
        assert _link("https://public.com") in result
        assert '<span class="spoiler"' in result

    def test_html_escaped(self):
        result = render_text("<b>bold</b>")
        assert "<b>" not in result
        assert "&lt;b&gt;" in result

    def test_strips_whitespace(self):
        assert render_text("  hello  ") == "hello"

    def test_empty_string(self):
        assert render_text("") == ""


class TestRenderRating:
    def test_none_returns_empty(self):
        assert render_rating(None) == ""

    def test_zero_returns_empty(self):
        assert render_rating(0) == ""

    def test_full_score_10_all_solid(self):
        result = render_rating(10)
        assert "🌕" * 5 in result
        assert "🌗" not in result
        assert "🌑" not in result

    def test_odd_score_9_has_half_star(self):
        result = render_rating(9)
        assert "🌕" * 4 in result
        assert "🌗" in result
        assert "🌑" not in result

    def test_even_score_8_has_empty_star(self):
        result = render_rating(8)
        assert "🌕" * 4 in result
        assert "🌗" not in result
        assert "🌑" * 1 in result

    def test_score_1_mostly_empty(self):
        result = render_rating(1)
        assert "🌗" in result
        assert "🌑" * 4 in result

    def test_result_is_padded_with_spaces(self):
        result = render_rating(10)
        assert result.startswith(" ")
        assert result.endswith(" ")

    def test_star_mode_1_uses_emoji_codes(self):
        from django.conf import settings

        result = render_rating(10, star_mode=1)
        assert settings.STAR_SOLID in result

    def test_star_mode_1_odd_score_has_half(self):
        from django.conf import settings

        result = render_rating(9, star_mode=1)
        assert settings.STAR_HALF in result


class TestRenderTitleAsHashtag:
    def test_plain_title(self):
        assert render_title_as_hashtag("Hello") == "#Hello"

    def test_spaces_become_underscores(self):
        assert render_title_as_hashtag("Hello World") == "#Hello_World"

    def test_hyphen_becomes_underscore(self):
        assert render_title_as_hashtag("sci-fi") == "#sci_fi"

    def test_leading_digit_gets_prefix(self):
        result = render_title_as_hashtag("12345")
        assert result.startswith("#t_")

    def test_multiple_spaces_collapsed(self):
        result = render_title_as_hashtag("a  b")
        assert "__" not in result

    def test_apostrophe_becomes_underscore(self):
        result = render_title_as_hashtag("Bourne's Identity")
        assert "#" in result
        assert "'" not in result

    def test_result_starts_with_hash(self):
        assert render_title_as_hashtag("anything").startswith("#")


class TestHasSpoiler:
    def test_spoiler_marker_detected(self):
        assert has_spoiler("check >! this out") is True

    def test_no_spoiler_marker(self):
        assert has_spoiler("no spoiler here") is False

    def test_empty_string(self):
        assert has_spoiler("") is False

    def test_partial_marker_not_detected(self):
        assert has_spoiler("just > and ! separate") is False


class TestHtmlToText:
    def test_strips_bold_tags(self):
        result = html_to_text("<b>bold</b>")
        assert "<b>" not in result
        assert "bold" in result

    def test_strips_paragraph_tags(self):
        result = html_to_text("<p>hello</p>")
        assert "<p>" not in result
        assert "hello" in result

    def test_unescapes_entities(self):
        result = html_to_text("&lt;tag&gt;")
        assert "<tag>" in result

    def test_br_becomes_newline(self):
        result = html_to_text("line1<br>line2")
        assert "\n" in result

    def test_closing_p_adds_newline(self):
        result = html_to_text("<p>a</p><p>b</p>")
        assert "\n" in result

    def test_plain_text_unchanged(self):
        result = html_to_text("just text")
        assert "just text" in result

    def test_empty_string(self):
        assert html_to_text("") == ""


class TestConvertLeadingSpaceInMd:
    def test_whitespace_only_line_becomes_empty(self):
        result = convert_leading_space_in_md("   ")
        assert result.strip() == ""

    def test_two_spaces_become_one_em_space(self):
        result = convert_leading_space_in_md("  hello")
        assert result == "\u2003hello"

    def test_four_spaces_become_two_em_spaces(self):
        result = convert_leading_space_in_md("    hello")
        assert result == "\u2003\u2003hello"

    def test_no_leading_spaces_unchanged(self):
        result = convert_leading_space_in_md("hello world")
        assert result == "hello world"

    def test_multiline_each_line_converted(self):
        result = convert_leading_space_in_md("  a\n  b")
        lines = result.split("\n")
        assert lines[0] == "\u2003a"
        assert lines[1] == "\u2003b"


@pytest.mark.django_db(databases="__all__")
class TestRenderSpoilerText:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.item = Edition.objects.create(title="Test Book")

    def test_no_spoiler_returns_none_and_original(self):
        spoiler, text = render_spoiler_text("normal text", self.item)
        assert spoiler is None
        assert text == "normal text"

    def test_empty_text_returns_none_and_empty(self):
        spoiler, text = render_spoiler_text("", self.item)
        assert spoiler is None
        assert text == ""

    def test_none_text_returns_none_and_empty(self):
        spoiler, text = render_spoiler_text(None, self.item)
        assert spoiler is None
        assert text == ""

    def test_spoiler_text_contains_item_title(self):
        spoiler, text = render_spoiler_text(">!hidden!<", self.item)
        assert spoiler is not None
        assert "Test Book" in spoiler

    def test_spoiler_markers_stripped_from_text(self):
        spoiler, text = render_spoiler_text("before >!secret!< after", self.item)
        assert ">!" not in text
        assert "!<" not in text
        assert "secret" in text


@pytest.mark.django_db(databases="__all__")
class TestRenderPostWithMacro:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.item = Edition.objects.create(title="My Book")

    def test_empty_template_returns_empty(self):
        assert render_post_with_macro("", self.item) == ""

    def test_title_placeholder_replaced(self):
        result = render_post_with_macro("Read [title]", self.item)
        assert "My Book" in result
        assert "[title]" not in result

    def test_hashtag_title_placeholder_replaced(self):
        result = render_post_with_macro("#[title]", self.item)
        assert "#" in result
        assert "[title]" not in result

    def test_url_placeholder_replaced(self):
        result = render_post_with_macro("[url]", self.item)
        assert "[url]" not in result
        assert "http" in result

    def test_category_placeholder_replaced(self):
        result = render_post_with_macro("[category]", self.item)
        assert "[category]" not in result

    def test_no_placeholders_unchanged(self):
        result = render_post_with_macro("just a post", self.item)
        assert result == "just a post"


class TestNormalizeImageSrc:
    """Tests for _normalize_image_src with default MEDIA_URL='/m/'."""

    def test_valid_media_path(self):
        result = _normalize_image_src("/m/upload/1/2025/abc.jpg")
        assert result == "/m/upload/1/2025/abc.jpg"

    def test_external_url_allowed(self):
        result = _normalize_image_src("https://example.com/photo.jpg")
        assert result == "https://example.com/photo.jpg"

    def test_relative_path_invalid(self):
        assert _normalize_image_src("relative/path.jpg") is None

    def test_absolute_path_not_under_media_invalid(self):
        assert _normalize_image_src("/static/secret.txt") is None

    def test_data_scheme_invalid(self):
        assert _normalize_image_src("data:image/png;base64,abc") is None

    def test_ftp_scheme_invalid(self):
        assert _normalize_image_src("ftp://example.com/file.jpg") is None

    def test_our_domain_media_path_valid(self, settings):
        settings.SITE_DOMAINS = ["mysite.local"]
        result = _normalize_image_src("https://mysite.local/m/upload/1/abc.jpg")
        assert result == "/m/upload/1/abc.jpg"

    def test_our_domain_non_media_path_invalid(self, settings):
        settings.SITE_DOMAINS = ["mysite.local"]
        assert _normalize_image_src("https://mysite.local/admin/") is None

    def test_alt_domain_media_path_valid(self, settings):
        settings.SITE_DOMAINS = ["mysite.local", "alt.mysite.local"]
        result = _normalize_image_src("https://alt.mysite.local/m/upload/1/abc.jpg")
        assert result == "/m/upload/1/abc.jpg"

    def test_empty_src_invalid(self):
        assert _normalize_image_src("") is None

    def test_bare_filename_invalid(self):
        assert _normalize_image_src("image.jpg") is None


class TestNormalizeImageSrcS3:
    """Tests for _normalize_image_src with S3 MEDIA_URL."""

    @pytest.fixture(autouse=True)
    def setup_s3(self, settings):
        settings.MEDIA_URL = "https://cdn.example.com/m/"

    def test_s3_url_valid(self):
        src = "https://cdn.example.com/m/upload/1/2025/abc.jpg"
        result = _normalize_image_src(src)
        assert result == src

    def test_s3_host_wrong_path_invalid(self):
        assert _normalize_image_src("https://cdn.example.com/other/file.jpg") is None

    def test_absolute_path_under_media_normalized_to_s3(self):
        result = _normalize_image_src("/m/upload/1/2025/abc.jpg")
        assert result == "https://cdn.example.com/m/upload/1/2025/abc.jpg"

    def test_absolute_path_not_under_media_invalid(self):
        assert _normalize_image_src("/static/file.jpg") is None

    def test_external_url_allowed(self):
        result = _normalize_image_src("https://other.com/photo.jpg")
        assert result == "https://other.com/photo.jpg"

    def test_our_domain_media_path_normalized(self, settings):
        settings.SITE_DOMAINS = ["mysite.local"]
        result = _normalize_image_src("https://mysite.local/m/upload/1/abc.jpg")
        assert result == "https://cdn.example.com/m/upload/1/abc.jpg"

    def test_our_domain_non_media_invalid(self, settings):
        settings.SITE_DOMAINS = ["mysite.local"]
        assert _normalize_image_src("https://mysite.local/admin/") is None


class TestSanitizeMdImages:
    def test_valid_image_kept(self):
        md = "![alt](/m/upload/1/2025/abc.jpg)"
        assert sanitize_md_images(md) == md

    def test_external_image_kept(self):
        md = "![photo](https://example.com/photo.jpg)"
        assert sanitize_md_images(md) == md

    def test_invalid_image_replaced_with_highlight(self):
        md = "![bad](relative/path.jpg)"
        result = sanitize_md_images(md)
        assert "==[invalid image: relative/path.jpg]==" in result
        assert "![" not in result

    def test_mixed_valid_and_invalid(self):
        md = "![ok](/m/upload/1/abc.jpg) and ![bad](/etc/passwd)"
        result = sanitize_md_images(md)
        assert "![ok](/m/upload/1/abc.jpg)" in result
        assert "==[invalid image: /etc/passwd]==" in result

    def test_no_images_unchanged(self):
        md = "just text with **bold** and [link](https://example.com)"
        assert sanitize_md_images(md) == md

    def test_multiple_invalid_images(self):
        md = "![a](bad1.jpg) ![b](bad2.jpg)"
        result = sanitize_md_images(md)
        assert "==[invalid image: bad1.jpg]==" in result
        assert "==[invalid image: bad2.jpg]==" in result

    def test_our_domain_normalized(self, settings):
        settings.SITE_DOMAINS = ["mysite.local"]
        md = "![img](https://mysite.local/m/upload/1/abc.jpg)"
        result = sanitize_md_images(md)
        assert result == "![img](/m/upload/1/abc.jpg)"
