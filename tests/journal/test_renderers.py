from journal.models.renderers import _linkify, render_text


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
