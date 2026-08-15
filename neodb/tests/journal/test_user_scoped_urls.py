"""Piece pages served under the owner's profile path.

standard.site forms a document's canonical URL by joining the publication
``url`` (the owner's profile) with the document ``path``, so articles,
reviews and collections are reachable at ``/users/<handle>/<type>/<uuid>``
as well as their canonical ``/<type>/<uuid>``. The alias must serve the
same page, keep pointing ``rel=canonical`` at the canonical form, and 404
for a handle that does not own the piece -- otherwise a document could
claim someone else's publication.
"""

import pytest
from django.test import Client
from django.utils.http import http_date

from catalog.models import Edition
from journal.models import Article, Collection, Review
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestUserScopedPieceUrls:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="scoped@test.com", username="scoped")
        self.other = User.register(email="stranger@test.com", username="stranger")
        self.identity = self.user.identity
        self.client = Client()
        self.article = Article.update_local_article(
            owner=self.identity, title="Scoped Essay", body="**bold**", visibility=0
        )
        book = Edition.objects.create(title="Dune")
        self.review = Review.update_item_review(
            book, self.identity, "Scoped Review", "review body"
        )
        assert self.review is not None
        self.collection = Collection.objects.create(
            owner=self.identity, title="Scoped List", brief="", visibility=0
        )

    def _scoped(self, piece, handle=None):
        return f"/users/{handle or self.identity.handle}{piece.url}"

    def test_article_served_under_owner_profile_path(self):
        resp = self.client.get(self._scoped(self.article))

        assert resp.status_code == 200
        assert b"Scoped Essay" in resp.content
        assert b"<strong>bold</strong>" in resp.content

    def test_review_served_under_owner_profile_path(self):
        resp = self.client.get(self._scoped(self.review))

        assert resp.status_code == 200
        assert b"Scoped Review" in resp.content

    def test_collection_served_under_owner_profile_path(self):
        resp = self.client.get(self._scoped(self.collection))

        assert resp.status_code == 200
        assert b"Scoped List" in resp.content

    @pytest.mark.parametrize("piece_name", ["article", "review", "collection"])
    def test_canonical_link_stays_on_canonical_url(self, piece_name):
        piece = getattr(self, piece_name)

        resp = self.client.get(self._scoped(piece))

        assert resp.status_code == 200
        expected = f'<link rel="canonical" href="{piece.absolute_url}">'
        assert expected.encode() in resp.content

    @pytest.mark.parametrize("piece_name", ["article", "review", "collection"])
    def test_wrong_handle_is_not_found(self, piece_name):
        piece = getattr(self, piece_name)

        resp = self.client.get(self._scoped(piece, handle="stranger"))

        assert resp.status_code == 404

    @pytest.mark.parametrize("piece_name", ["article", "review", "collection"])
    def test_unknown_handle_is_not_found(self, piece_name):
        piece = getattr(self, piece_name)

        resp = self.client.get(self._scoped(piece, handle="nobody"))

        assert resp.status_code == 404

    def test_private_piece_under_wrong_handle_is_not_found(self):
        # 404 before the visibility check, so a mismatched handle cannot
        # tell a private piece apart from a missing one
        private = Article.update_local_article(
            owner=self.identity, title="Secret", body="body", visibility=2
        )

        assert (
            self.client.get(self._scoped(private, handle="stranger")).status_code == 404
        )

    @pytest.mark.parametrize("piece_name", ["article", "review", "collection"])
    def test_wrong_handle_not_served_from_conditional_cache(self, piece_name):
        # the Last-Modified callback runs before the view, so it has to
        # decline the mismatch too or a stale If-Modified-Since would get
        # a 304 instead of the 404
        piece = getattr(self, piece_name)
        resp = self.client.get(
            self._scoped(piece, handle="stranger"),
            HTTP_IF_MODIFIED_SINCE=_future_date(piece),
        )

        assert resp.status_code == 404


def _future_date(piece):
    return http_date(piece.edited_time.timestamp() + 3600)
