"""Tests for ~neodb~ placeholder URL rewriting in web post rendering.

Posts federated by NeoDB embed item links as
``{site_url}/~neodb~{item_url}`` so consuming instances can localize
them. The takahe app rewrites these for its own templates and the
Mastodon API; the mirror ``takahe.models.Post`` must do the same for
NeoDB web templates rendering ``safe_content_local``.
"""

from urllib.parse import quote

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.safestring import SafeString

from catalog.models import Edition
from journal.models import Mark, ShelfType
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
