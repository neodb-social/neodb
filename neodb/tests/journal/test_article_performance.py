"""Guard rendering and query costs as an author's history grows."""

from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connections
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from journal.models import Article, Review
from journal.models.article import _normalize_tags
from journal.models.renderers import render_md
from journal.views.article import _article_tag_suggestions
from users.models import User


@pytest.mark.parametrize("model", [Article, Review])
def test_body_is_rendered_once_and_edits_invalidate_cached_values(model):
    piece = model(title="Reading", body="Original **text** & 中文")
    with patch(f"{model.__module__}.render_md", wraps=render_md) as render:
        for _ in range(3):
            assert "<strong>text</strong>" in piece.html_content
            assert "text & 中文" in " ".join(piece.plain_content.split())
            if model is Article:
                assert piece.reading_time == 1
                assert "text & 中文" in piece.teaser
        assert render.call_count == 1

        piece.body = "Changed " * 800
        assert "Original" not in piece.plain_content
        assert "Changed" in piece.html_content
        if model is Article:
            assert piece.reading_time == 4
        assert render.call_count == 2


@pytest.mark.django_db(databases="__all__")
@pytest.mark.parametrize("view", ["profile_articles", "user_article_list"])
def test_lists_have_constant_queries_and_one_render_per_visible_article(view):
    user = User.register(email=f"{view}@example.com", username=view)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    url = reverse(f"journal:{view}", args=[user.identity.handle])

    def add_articles(start, stop):
        for i in range(start, stop):
            Article(
                owner=user.identity,
                title=f"Article {i}",
                body="**Words** and 中文.\n\n" * 50,
                tags=["reading", f"topic{i % 3}"],
            ).save(post_when_save=False, index_when_save=False)

    counts = []
    for start, stop in [(0, 4), (4, 40)]:
        add_articles(start, stop)
        client.get(url)  # Warm template/content-type caches before measuring.
        with ExitStack() as stack:
            queries = [
                stack.enter_context(CaptureQueriesContext(connections[alias]))
                for alias in ("default", "takahe")
            ]
            render = stack.enter_context(
                patch("journal.models.article.render_md", wraps=render_md)
            )
            response = client.get(url)
        assert response.status_code == 200
        assert render.call_count == len(response.context["articles"])
        counts.append([len(query) for query in queries])
        article_queries = [q for q in queries[0] if '"journal_article"' in q["sql"]]
        assert len(article_queries) == (2 if view == "profile_articles" else 4)
    assert counts[0] == counts[1]


@pytest.mark.django_db(databases="__all__")
def test_suggestions_process_distinct_tag_sets_in_recency_order():
    user = User.register(email="tagperf@example.com", username="tagperf")
    now = timezone.now()
    for i in range(40):
        Article(
            owner=user.identity,
            title=f"Article {i}",
            body="Words",
            tags=["#old  tag"] if i % 2 else ["recent"],
            edited_time=now - timedelta(days=i),
        ).save(post_when_save=False, index_when_save=False)
    with (
        CaptureQueriesContext(connections["default"]) as queries,
        patch(
            "journal.views.article._normalize_tags", wraps=_normalize_tags
        ) as normalize,
    ):
        suggestions = _article_tag_suggestions(user.identity)
    assert suggestions == ["recent", "old tag"]
    assert len(queries) == 1
    assert normalize.call_count == 2
