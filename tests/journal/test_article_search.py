"""Regression tests for the ``item_id > 0`` gate in journal search that
silently dropped Articles for any query missing ``type:article`` — including
the tag chips that link from ``/article/<uuid>`` to
``/search?c=journal&q=tag:"<title>"``."""

import pytest
from django.test import Client

from catalog.models import Edition
from journal.models import Article, Mark, ShelfType
from journal.search import JournalIndex
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestArticleJournalSearch:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.index = JournalIndex.instance()
        self.index.delete_all()
        self.user = User.register(email="art_search@test.com", username="art_search")
        self.identity = self.user.identity
        self.client = Client()
        self.client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")

    def _make_article(self, *, title, body, tags=None):
        return Article.update_local_article(
            owner=self.identity,
            title=title,
            body=body,
            tags=tags or [],
            visibility=0,
        )

    def test_tag_search_surfaces_article(self):
        """The tag chips on /article/<uuid> link at
        ``/search?c=journal&q=tag:"foo"``. Before the fix, this gate
        excluded articles via ``item_id > 0`` and returned nothing."""
        article = self._make_article(
            title="Findable Article",
            body="indexable body content",
            tags=["draftnote"],
        )
        resp = self.client.get('/search?c=journal&q=tag:"draftnote"')
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Findable Article" in body
        assert article.url in body

    def test_free_text_search_surfaces_article(self):
        """Free-text journal search must also return articles, not just
        item-keyed pieces."""
        article = self._make_article(
            title="Unique Article Title",
            body="zzqxyz this body has a uniquely findable token",
        )
        resp = self.client.get("/search?c=journal&q=zzqxyz")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert article.url in body

    def test_type_article_filter_still_works(self):
        """``type:article`` should keep working unchanged."""
        a1 = self._make_article(title="Type Filtered", body="alpha beta")
        # An item-keyed Mark should NOT appear when filtering by article.
        book = Edition.objects.create(title="Some Book")
        Mark(self.identity, book).update(
            ShelfType.WISHLIST, "alpha beta gamma", None, [], 0
        )
        resp = self.client.get("/search?c=journal&q=type:article alpha")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert a1.url in body
        # The mark's catalog item shouldn't appear in the article-only view.
        assert "Some Book" not in body

    def test_search_renders_article_teaser_partial(self):
        """Results should reuse ``_article_teaser.html`` so the rendering
        matches the timeline (title link + display_summary + word count)."""
        article = self._make_article(
            title="Teaser-rendered",
            body="word " * 50,  # 50 words for predictable word_count
            tags=["t1"],
        )
        resp = self.client.get('/search?c=journal&q=tag:"t1"')
        assert resp.status_code == 200
        body = resp.content.decode()
        # _article_teaser.html renders the title inside an <h4> with an
        # <a> to the article URL — pin both so the include path can't
        # silently regress to bespoke markup.
        assert f'href="{article.url}"' in body
        assert "Teaser-rendered" in body
