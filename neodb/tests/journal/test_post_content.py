"""Tests for ~neodb~ placeholder URL rewriting in web post rendering.

Posts federated by NeoDB embed item links as
``{site_url}/~neodb~{item_url}`` so consuming instances can localize
them. The takahe app rewrites these for its own templates and the
Mastodon API; the mirror ``takahe.models.Post`` must do the same for
NeoDB web templates rendering ``safe_content_local``.
"""

import pytest

from catalog.models import Edition
from journal.models import Mark, ShelfType
from takahe.models import Post
from users.models import User


class TestRewriteNeodbUrls:
    def test_rewrites_remote_placeholder_href(self):
        content = '<a href="https://remote.example/~neodb~/movie/abc">Title</a>'
        assert Post._rewrite_neodb_urls(content) == (
            '<a href="https://example.org/search?r=1&q=https://remote.example/movie/abc">Title</a>'
        )

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
    rendered = post.safe_content_local
    assert "~neodb~" not in rendered
    assert f'href="https://example.org/search?r=1&q=https://example.org{book.url}"' in (
        rendered
    )
