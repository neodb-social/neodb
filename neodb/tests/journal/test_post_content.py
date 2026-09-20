"""Tests for ~neodb~ placeholder URL rewriting in web post rendering.

Posts federated by NeoDB embed item links as
``{site_url}/~neodb~{item_url}`` so consuming instances can localize
them. The takahe app rewrites these for its own templates and the
Mastodon API; the mirror ``takahe.models.Post`` must do the same for
NeoDB web templates rendering ``safe_content_local``.
"""

from pathlib import Path
from urllib.parse import quote

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.safestring import SafeString

from catalog.models import Edition
from journal.models import Mark, ShelfType
from takahe import html as neodb_html
from takahe.html import FediverseHtmlParser
from takahe.models import Post
from users.models import User


class TestRewriteNeodbUrls:
    def test_rewrites_remote_placeholder_href(self):
        content = '<a href="https://remote.example/~neodb~/movie/abc">Title</a>'
        result = Post._rewrite_neodb_urls(content)
        assert result == (
            '<a href="https://example.org/search?r=1&q=https%3A%2F%2Fremote.example%2Fmovie%2Fabc">Title</a>'
        )

    def test_result_stays_html_safe(self):
        # templates render safe_content_local without |safe, so the rewrite
        # must not strip the SafeString marker set by ContentRenderer
        assert isinstance(Post._rewrite_neodb_urls("<p>hi</p>"), SafeString)

    def test_leaves_plain_links_unchanged(self):
        content = '<a href="https://remote.example/movie/abc">Title</a>'
        assert Post._rewrite_neodb_urls(content) == content


@pytest.mark.django_db(databases="__all__")
def test_safe_content_local_rewrites_item_link():
    book = Edition.objects.create(title="Rewrite Test Book")
    user = User.register(email="rewrite@test.com", username="rewrite_user")
    Mark(user.identity, book).update(ShelfType.WISHLIST, "note", None, [], 0)
    shelfmember = Mark(user.identity, book).shelfmember
    assert shelfmember is not None
    post = shelfmember.latest_post
    assert post is not None
    assert f"/~neodb~{book.url}" in post.content
    rewritten_href = (
        'href="https://example.org/search?r=1&q='
        f'{quote(f"https://example.org{book.url}", safe="")}"'
    )
    rendered = post.safe_content_local
    assert "~neodb~" not in rendered
    assert rewritten_href in rendered

    # the rewritten anchor must reach page HTML unescaped
    response = Client().get(
        reverse(
            "journal:user_post_list",
            kwargs={"user_name": user.identity.handle},
        )
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert rewritten_href in html
    assert "~neodb~" not in html


class TestMirrorParser:
    """The neodb mirror renders the web UI, so its own copy needs coverage.

    takahe/core/html.py is the file the takahe suite exercises; these run the
    same paths through the neodb import so the mirror cannot rot unnoticed.
    """

    def test_keeps_lists_quotes_and_code(self):
        parser = FediverseHtmlParser(
            "<p>a</p><ul><li>one</li><li>two</li></ul>"
            "<blockquote><p>q</p></blockquote><pre><code>x = 1\ny = 2</code></pre>"
        )
        assert parser.html == (
            "<p>a</p><ul><li>one</li><li>two</li></ul>"
            "<blockquote><p>q</p></blockquote><pre><code>x = 1\ny = 2</code></pre>"
        )
        assert parser.plain_text == "a\n\none\ntwo\n\nq\n\n\n\nx = 1\ny = 2"

    def test_demotes_headings_and_keeps_inline(self):
        parser = FediverseHtmlParser(
            "<h3>T</h3><p><strong>b</strong><em>i</em><code>c</code><del>d</del></p>"
        )
        assert parser.html == (
            "<p><strong>T</strong></p>"
            "<p><strong>b</strong><em>i</em><code>c</code><del>d</del></p>"
        )

    def test_balances_unclosed_remote_markup(self):
        assert FediverseHtmlParser("<p>a</p><pre>rest").html == (
            "<p>a</p><pre>rest</pre>"
        )
        assert FediverseHtmlParser("<ul><li>a<li>b").html == (
            "<ul><li>a</li><li>b</li></ul>"
        )
        assert FediverseHtmlParser("</ul></p><p>ok</p>").html == "<p>ok</p>"

    def test_does_not_linkify_inside_code(self):
        parser = FediverseHtmlParser(
            "<pre><code>#tag https://example.com/x</code></pre>", find_hashtags=True
        )
        assert parser.html == "<pre><code>#tag https://example.com/x</code></pre>"
        assert parser.hashtags == set()

    def test_drops_attributes_and_unknown_tags(self):
        parser = FediverseHtmlParser('<ul onclick="evil()"><li class="x">y</li></ul>')
        assert parser.html == "<ul><li>y</li></ul>"
        assert FediverseHtmlParser("<table><tr><td>c</td></tr></table>").html == "c"

    def test_mirror_matches_takahe_copy(self):
        """Only the Emoji import may differ, or the two renderers diverge."""
        mirror = Path(neodb_html.__file__)
        # repo root, whether run from a checkout or the dev container mounts
        original = mirror.parents[2] / "takahe" / "core" / "html.py"
        if not original.exists():
            pytest.skip(f"takahe checkout not present at {original}")
        neodb_src = mirror.read_text()
        normalized = original.read_text().replace(
            "from activities.models import Emoji", "from .models import Emoji"
        )
        assert neodb_src == normalized, (
            "neodb/takahe/html.py and takahe/core/html.py have drifted; "
            "keep them identical apart from the Emoji import"
        )
