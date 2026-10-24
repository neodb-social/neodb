"""Tests for the article list page (``journal:user_article_list``)."""

from datetime import datetime, timezone as datetime_timezone

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

    # an out-of-range page is clamped (Django's get_page sends anything below
    # 1 to the last page), and the links follow the page that was rendered
    # rather than the one that was asked for
    clamped = client.get(_url(user), {"page": 0}).content.decode()
    assert clamped.count('class="article-entry"') == 1
    assert 'aria-current="page">2</span>' in clamped
    assert 'aria-current="page">1</span>' not in clamped


def test_tag_links_are_url_encoded():
    user, client = _member("reserved")
    _article(user.identity, "Reserved", tags=["R&D", "C#"])

    content = client.get(_url(user)).content.decode()

    assert "tag=R%26D" in content
    assert "tag=C%23" in content
    # the raw form would end the query at "&" and start a fragment at "#"
    assert "q=tag:&quot;R&amp;D&quot;" not in content

    # Readers use the author's archive, not their own journal search.
    public = Client().get(_url(user)).content.decode()
    assert "tag=R%26D" in public
    filtered = Client().get(_url(user), {"tag": "R&D"})
    assert filtered.context["total"] == 1


def _dated_article(owner, title, year, month, **kwargs):
    article = _article(owner, title, **kwargs)
    Article.objects.filter(pk=article.pk).update(
        created_time=datetime(year, month, 15, 12, tzinfo=datetime_timezone.utc)
    )
    return article


def test_tag_and_date_filter_before_pagination(monkeypatch):
    import common.utils

    monkeypatch.setattr(common.utils, "ITEMS_PER_PAGE", 1)
    monkeypatch.setattr(common.utils, "ITEMS_PER_PAGE_OPTIONS", [1])
    user, client = _member("filterer")
    first = _dated_article(user.identity, "September A", 2026, 9, tags=["Reading"])
    second = _dated_article(user.identity, "September B", 2026, 9, tags=["Reading"])
    _dated_article(user.identity, "October", 2026, 10, tags=["Reading"])
    _dated_article(user.identity, "Cinema", 2026, 9, tags=["Film"])
    _dated_article(user.identity, "Last year", 2025, 9, tags=["Reading"])
    response = client.get(_url(user), {"tag": "Reading", "date": "2026-09"})
    assert response.context["total"] == 2
    assert response.context["articles"] == [second]
    assert not response.context["show_featured"]
    html = response.content.decode()
    assert "tag=Reading&amp;date=2026-09&amp;page=2" in html
    page_two = client.get(_url(user), {"tag": "Reading", "date": "2026-09", "page": 2})
    assert page_two.context["articles"] == [first]
    assert client.get(_url(user), {"date": "2026"}).context["total"] == 4
    assert client.get(_url(user), {"tag": "Film", "date": "2025"}).context["total"] == 0
    # A facet selection resets the page while preserving the other filter.
    assert '?tag=Film&amp;date=2026-09"' in page_two.content.decode()


def test_archive_facets_do_not_leak_private_articles():
    owner, own_client = _member("privatearchive")
    _, visitor = _member("archivevisitor")
    _dated_article(owner.identity, "Public", 2026, 9, tags=["Public tag"])
    hidden = _dated_article(owner.identity, "Private", 2024, 3, tags=["Secret tag"])
    Article.objects.filter(pk=hidden.pk).update(visibility=2)
    for client in (Client(), visitor):
        response = client.get(_url(owner))
        assert response.context["article_tags"] == [{"name": "Public tag", "count": 1}]
        assert [year["value"] for year in response.context["archive_years"]] == ["2026"]
        assert (
            client.get(_url(owner), {"tag": "Secret tag", "date": "2024"}).context[
                "total"
            ]
            == 0
        )
    own = own_client.get(_url(owner))
    assert len(own.context["article_tags"]) == 2
    assert (
        own_client.get(_url(owner), {"tag": "Secret tag", "date": "2024-03"}).context[
            "total"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "date_value", ["oops", "2026-13", "2026-00", "0000", "9999", "2026-09-01"]
)
def test_invalid_archive_date_is_ignored(date_value):
    owner, client = _member("invaliddate")
    _article(owner.identity, "Still visible")
    response = client.get(_url(owner), {"date": date_value})
    assert response.status_code == 200
    assert response.context["selected_date"] == ""
    assert response.context["total"] == 1


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


def test_legacy_tags_share_facets_links_and_filters():
    user, client = _member("legacytags")
    first = _article(user.identity, "Legacy")
    second = _article(user.identity, "Current", tags=["Film noir"])
    # Older/imported rows can bypass canonicalization.
    Article.objects.filter(pk=first.pk).update(
        tags=["#Film  noir", "Film noir", "  #Film noir ", None]
    )
    response = client.get(_url(user))
    assert response.context["article_tags"] == [{"name": "Film noir", "count": 2}]
    filtered = client.get(_url(user), {"tag": "Film noir"})
    assert {article.pk for article in filtered.context["articles"]} == {
        first.pk,
        second.pk,
    }
    assert filtered.context["total"] == 2
    suggestions = client.get(reverse("journal:article_compose")).context[
        "tag_suggestions"
    ]
    assert suggestions == ["Film noir"]


def test_archive_uses_thumbnails_and_does_not_prefetch_post_interactions(monkeypatch):
    from unittest.mock import Mock
    import common.templatetags.thumb as thumbnail_tags
    from takahe.utils import Takahe

    user, client = _member("thumbnails")
    local = _article(user.identity, "Photo")
    Article.objects.filter(pk=local.pk).update(cover="large-photo.jpg")
    remote = _article(user.identity, "Remote image")
    Article.objects.filter(pk=remote.pk).update(
        metadata={"cover_url": "https://remote.example/cover.jpg"}
    )
    thumbnail = Mock(return_value="/small-photo.jpg")
    interactions = Mock()
    monkeypatch.setattr(thumbnail_tags, "thumbnail_url", thumbnail)
    monkeypatch.setattr(Takahe, "prefetch_interaction_flags", interactions)
    content = client.get(_url(user)).content.decode()
    assert 'src="/small-photo.jpg"' in content
    assert 'src="https://remote.example/cover.jpg"' in content
    assert thumbnail.call_args.args[1] == "normal"
    interactions.assert_not_called()
