"""Tests for the article list page (``journal:user_article_list``)."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from catalog.models import Edition
from journal.models import Article, Review
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _member(username: str) -> tuple[User, Client]:
    user = User.register(email=f"{username}@example.com", username=username)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, client


def _url(user: User) -> str:
    return reverse("journal:user_article_list", args=[user.identity.handle])


def _article(owner, title: str, body: str = "Some words here.", **kwargs) -> Article:
    return Article.update_local_article(
        owner=owner, title=title, body=body, visibility=0, **kwargs
    )


def test_list_page_renders_a_card_per_article():
    user, client = _member("lister")
    _article(user.identity, "First one", "The body of the first one.", tags=["alpha"])
    _article(user.identity, "Second one", "The body of the second one.")

    content = client.get(_url(user)).content.decode()

    assert 'class="article-list"' in content
    assert content.count('class="article-entry"') == 2
    assert "First one" in content
    assert "The body of the first one." in content
    assert "min read" in content
    # the year label that groups the cards
    assert f'class="article-year">{timezone.now().year}<' in content
    assert "alpha" in content
    # the old bare heading is gone
    assert "<h5>" not in content
    # and a single page carries no page links
    assert "page=2" not in content


def test_list_page_shows_compose_and_feed_only_where_they_apply():
    user, client = _member("owner")
    _article(user.identity, "Mine")
    _, other_client = _member("reader")

    own = client.get(_url(user)).content.decode()
    assert reverse("journal:article_compose") in own
    assert "feed/articles/" in own

    seen_by_other = other_client.get(_url(user)).content.decode()
    assert reverse("journal:article_compose") not in seen_by_other


def test_sensitive_article_previews_its_marker_not_its_body():
    user, client = _member("careful")
    _article(
        user.identity, "Wrapped", "A detail that stays behind the cue.", sensitive=True
    )

    content = client.get(_url(user)).content.decode()

    assert "A detail that stays behind the cue." not in content
    assert "sensitive content" in content
    # and no cover of it either, the detail page hides both together
    assert 'class="thumb"' not in content


def test_teaser_decodes_character_references():
    user, client = _member("ampersand")
    article = _article(user.identity, "Notes", "Features & interaction, a > b.")

    # markdown escapes the characters; plain text must carry them decoded
    assert "Features & interaction, a > b." in article.plain_content
    assert "&amp;" not in article.teaser

    content = client.get(_url(user)).content.decode()
    # escaped exactly once for HTML, not twice
    assert "Features &amp; interaction, a &gt; b." in content
    assert "&amp;amp;" not in content


def test_list_page_paginates(monkeypatch):
    import common.utils

    monkeypatch.setattr(common.utils, "ITEMS_PER_PAGE", 2)
    monkeypatch.setattr(common.utils, "ITEMS_PER_PAGE_OPTIONS", [2])
    user, client = _member("prolific")
    for i in range(3):
        _article(user.identity, f"Article {i}")

    first = client.get(_url(user)).content.decode()
    assert first.count('class="article-entry"') == 2
    assert "page=2" in first

    second = client.get(_url(user), {"page": 2}).content.decode()
    assert second.count('class="article-entry"') == 1
    # ordered newest first, so the oldest is alone on the last page
    assert "Article 0" in second
    assert "Article 2" not in second


def test_list_page_is_empty_for_a_user_without_articles():
    user, client = _member("quiet")

    own = client.get(_url(user)).content.decode()
    assert 'class="dc-empty"' in own
    assert reverse("journal:article_compose") in own

    _, other_client = _member("visitor")
    seen_by_other = other_client.get(_url(user)).content.decode()
    assert 'class="dc-empty"' in seen_by_other
    assert reverse("journal:article_compose") not in seen_by_other


def test_list_page_is_readable_by_an_anonymous_visitor():
    user, _ = _member("public")
    _article(user.identity, "Open to all", "Anyone may read this.")

    response = Client().get(_url(user))

    assert response.status_code == 200
    assert "Open to all" in response.content.decode()


def test_reading_time_counts_cjk_by_character():
    user, _ = _member("bilingual")
    latin = _article(user.identity, "Latin", "word " * 600)
    cjk = _article(user.identity, "CJK", "字" * 800)

    assert latin.reading_time == 3
    assert cjk.reading_time == 2
    # the whitespace token count cannot tell these apart
    assert cjk.word_count == 1


def test_review_plain_text_decodes_character_references():
    """``Review.plain_content`` carries the same fix as the article one."""
    user, _ = _member("reviewer")
    book = Edition.objects.create(title="A Book", author=["An Author"])
    review = Review.update_item_review(
        book, user.identity, "Notes", "Features & interaction, a > b."
    )

    assert review is not None
    assert "Features & interaction, a > b." in review.plain_content
    assert "&amp;" not in review.brief_description
