"""Tests for the standalone Article piece (item-less, markdown-authored)."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.utils import timezone

from journal.models import Article
from journal.search import JournalQueryParser
from takahe.ap_handlers import post_deleted
from takahe.utils import Takahe
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestArticleModel:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art@test.com", username="art_user")
        self.identity = self.user.identity

    def test_article_create_basic(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Hello",
            body="**Bold**",
            tags=["alpha", "beta"],
            visibility=0,
        )
        assert article.pk
        assert article.title == "Hello"
        assert article.body == "**Bold**"
        assert article.normalized_tags == ["alpha", "beta"]
        assert article.url.startswith("/article/")

    def test_article_ap_object_uses_markdown_source(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Title",
            body="**Bold** text",
            tags=["foo"],
            visibility=0,
        )
        obj = article.ap_object
        assert obj["type"] == "Article"
        assert obj["name"] == "Title"
        assert obj["source"] == {
            "content": "**Bold** text",
            "mediaType": "text/markdown",
        }
        assert "<strong>Bold</strong>" in obj["content"]
        tag_names = {t["name"] for t in obj["tag"] if t.get("type") == "Hashtag"}
        assert "#foo" in tag_names

    def test_article_get_ap_data_omits_relatedwith(self):
        """Standalone Article must NOT carry `relatedWith` so it is not
        confused with Reviews-as-Article on receiving servers."""
        article = Article.update_local_article(
            owner=self.identity,
            title="Title",
            body="Body",
            visibility=0,
        )
        data = article.get_ap_data()
        assert "object" in data
        assert data["object"]["type"] == "Article"
        assert "relatedWith" not in data["object"]
        assert "tag" in data["object"]  # Hashtag list (possibly empty)

    def test_article_post_type_is_article(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Long Form",
            body="Body content",
            visibility=0,
        )
        post_id = article.latest_post_id
        assert post_id is not None
        post = Takahe.get_post(post_id)
        assert post is not None
        assert post.type == "Article"
        assert post.type_data["object"]["name"] == "Long Form"
        assert post.type_data["object"]["source"]["mediaType"] == "text/markdown"
        # No relatedWith on standalone article
        assert "relatedWith" not in post.type_data["object"]

    def test_article_edit_reuses_post(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="V1",
            body="First",
            visibility=0,
        )
        post_id_v1 = article.latest_post_id
        article = Article.update_local_article(
            owner=self.identity,
            title="V2",
            body="Second",
            visibility=0,
            article=article,
        )
        post_id_v2 = article.latest_post_id
        assert post_id_v1 == post_id_v2  # edit, not recreate
        post = Takahe.get_post(post_id_v2)
        assert post is not None
        assert post.type_data["object"]["name"] == "V2"

    def test_article_delete_clears_post(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="To Delete",
            body="Bye",
            visibility=0,
        )
        post_id = article.latest_post_id
        assert post_id is not None
        article.delete()
        post = Takahe.get_post(post_id)
        # Post may still exist briefly but must be in a deleted state.
        assert post is None or post.state in ("deleted", "deleted_fanned_out")

    def test_word_count_persisted_in_metadata(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Counted",
            body="one two three four five",
            visibility=0,
        )
        assert article.metadata.get("word_count") == 5
        assert article.word_count == 5

    def test_word_count_recomputed_on_edit(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="V1",
            body="alpha beta",
            visibility=0,
        )
        article = Article.update_local_article(
            owner=self.identity,
            title="V2",
            body="alpha beta gamma delta",
            visibility=0,
            article=article,
        )
        assert article.word_count == 4
        assert article.metadata["word_count"] == 4

    def test_word_count_falls_back_when_metadata_missing(self):
        """Rows that predate the field (or have metadata cleared) compute
        on demand rather than KeyError."""
        article = Article.update_local_article(
            owner=self.identity,
            title="Live",
            body="six seven eight nine",
            visibility=0,
        )
        article.metadata = {}
        article.save(update_fields=["metadata"], post_when_save=False)
        article.refresh_from_db()
        assert article.word_count == 4

    def test_excerpt_truncates_long_body(self):
        body = "word " * 200  # 1000 chars
        article = Article.update_local_article(
            owner=self.identity,
            title="Long",
            body=body,
            visibility=0,
        )
        assert len(article.excerpt) <= 222
        assert article.excerpt.endswith("…")

    def test_display_summary_no_marker_when_not_sensitive(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Calm",
            body="body",
            summary="A quiet take.",
            sensitive=False,
            visibility=0,
        )
        assert article.display_summary == "A quiet take."

    def test_display_summary_appends_marker_when_sensitive(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Spicy",
            body="body",
            summary="A hot take.",
            sensitive=True,
            visibility=0,
        )
        assert "A hot take." in article.display_summary
        assert "(may contain sensitive content)" in article.display_summary

    def test_display_summary_marker_only_when_no_summary(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="No summary",
            body="body",
            summary="",
            sensitive=True,
            visibility=0,
        )
        assert article.display_summary == "(may contain sensitive content)"

    def test_ap_object_summary_is_raw_for_round_trip(self):
        """``ap_object`` carries the raw author summary (no auto-marker)
        so NDJSON / inbound-AP round trips don't accumulate the
        sensitivity suffix on each cycle."""
        article = Article.update_local_article(
            owner=self.identity,
            title="t",
            body="b",
            summary="cap",
            sensitive=True,
            visibility=0,
        )
        assert article.ap_object["summary"] == "cap"

    def test_get_ap_data_summary_uses_display_summary(self):
        """The federation envelope (``get_ap_data``) carries the
        decorated ``display_summary`` so receivers see the sensitivity
        cue alongside the user's text."""
        article = Article.update_local_article(
            owner=self.identity,
            title="t",
            body="b",
            summary="cap",
            sensitive=True,
            visibility=0,
        )
        wire_summary = article.get_ap_data()["object"]["summary"]
        assert "cap" in wire_summary
        assert "(may contain sensitive content)" in wire_summary

    def test_excerpt_short_body_unchanged(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Short",
            body="just a few words here",
            visibility=0,
        )
        assert article.excerpt == "just a few words here"

    def test_article_to_indexable_doc(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Searchable",
            body="Some body",
            tags=["foo", "bar"],
            visibility=0,
        )
        doc = article.to_indexable_doc()
        assert "Searchable" in doc["content"]
        assert "Some body" in doc["content"]
        assert sorted(doc["tag"]) == ["bar", "foo"]


@pytest.mark.django_db(databases="__all__")
class TestArticleSearchParser:
    def test_article_in_type_values(self):
        parser = JournalQueryParser("type:article foo", page=1)
        assert parser.filter_by.get("piece_class") == ["article"]


@pytest.mark.django_db(databases="__all__")
class TestArticleParamsFromAp:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art2@test.com", username="art_user2")
        self.identity = self.user.identity

    def test_params_uses_source_when_markdown(self):
        post = MagicMock()
        post.local = False
        post.language = "en"
        ap = {
            "type": "Article",
            "name": "Hello",
            "content": "<p>HTML rendered</p>",
            "source": {"content": "**Hello**", "mediaType": "text/markdown"},
            "tag": [
                {"type": "Hashtag", "name": "#one"},
                {"type": "Hashtag", "name": "#two"},
            ],
            "sensitive": True,
            "summary": "CW",
        }
        params = Article.params_from_ap_object(post, ap, None)
        assert params["title"] == "Hello"
        assert params["body"] == "**Hello**"
        assert params["sensitive"] is True
        assert params["summary"] == "CW"
        assert params["language"] == "en"
        assert sorted(params["tags"]) == ["one", "two"]

    def test_params_falls_back_to_html(self):
        post = MagicMock()
        post.local = False
        post.language = ""
        ap = {
            "type": "Article",
            "name": "Hi",
            "content": "<p>Plain HTML</p>",
        }
        params = Article.params_from_ap_object(post, ap, None)
        assert params["title"] == "Hi"
        assert "Plain HTML" in params["body"]


def _make_remote_post(identity_pk: int, post_id: int = 99001) -> MagicMock:
    """Mock a remote Takahe Post for ``Article.update_by_ap_object``."""
    post = MagicMock()
    post.local = False
    post.visibility = 0  # Takahe numeric for public
    post.id = post_id
    post.author_id = identity_pk
    post.summary = None
    post.sensitive = False
    post.language = "en"
    post.attachments.all.return_value = []
    return post


@pytest.mark.django_db(databases="__all__")
class TestArticleInboundParsing:
    """``Article.update_by_ap_object`` persists remote standalone Articles."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art_in@test.com", username="art_in")
        self.identity = self.user.identity

    def _ap_obj(self, *, name="Remote Article", body="**hello**") -> dict:
        published = timezone.now() - timedelta(hours=1)
        return {
            "id": "https://remote.example/article/abc",
            "type": "Article",
            "name": name,
            "content": "<p>fallback HTML</p>",
            "source": {"content": body, "mediaType": "text/markdown"},
            "tag": [{"type": "Hashtag", "name": "#one"}],
            "sensitive": False,
            "published": published.isoformat(),
            "updated": published.isoformat(),
            "attributedTo": self.identity.actor_uri,
            "href": "https://remote.example/article/abc",
        }

    def test_creates_remote_article(self):
        post = _make_remote_post(self.identity.pk)
        ap = self._ap_obj()
        article = Article.update_by_ap_object(self.identity, None, ap, post)
        assert article is not None
        assert article.local is False
        assert article.remote_id == ap["id"]
        assert article.title == "Remote Article"
        assert article.body == "**hello**"
        assert article.normalized_tags == ["one"]
        # Linked to the originating Post.
        assert post.id in article.all_post_ids

    def test_local_post_is_skipped(self):
        """Article.update_by_ap_object never touches local-author posts —
        their authoritative path is ``update_local_article`` via the form."""
        post = _make_remote_post(self.identity.pk)
        post.local = True
        article = Article.update_by_ap_object(self.identity, None, self._ap_obj(), post)
        assert article is None
        assert not Article.objects.filter(owner=self.identity).exists()

    def test_owner_mismatch_rejected(self):
        post = _make_remote_post(self.identity.pk)
        ap = self._ap_obj()
        Article.update_by_ap_object(self.identity, None, ap, post)
        # Now arrive with the same post.id but a different author_id.
        post2 = _make_remote_post(self.identity.pk, post_id=post.id)
        post2.author_id = self.identity.pk + 99999
        result = Article.update_by_ap_object(self.identity, None, ap, post2)
        assert result is None

    def test_stale_update_is_noop(self):
        post = _make_remote_post(self.identity.pk)
        ap = self._ap_obj()
        first = Article.update_by_ap_object(self.identity, None, ap, post)
        assert first is not None
        original_title = first.title
        # Older `updated` than what we already have should be ignored.
        ap_old = dict(ap)
        ap_old["name"] = "Older Title"
        ap_old["updated"] = (timezone.now() - timedelta(days=365)).isoformat()
        result = Article.update_by_ap_object(self.identity, None, ap_old, post)
        assert result is not None
        assert result.title == original_title

    def test_newer_update_applies(self):
        post = _make_remote_post(self.identity.pk)
        ap = self._ap_obj()
        Article.update_by_ap_object(self.identity, None, ap, post)
        ap_new = dict(ap)
        ap_new["name"] = "Updated Title"
        ap_new["source"] = {"content": "_changed_", "mediaType": "text/markdown"}
        ap_new["updated"] = (timezone.now() + timedelta(seconds=1)).isoformat()
        result = Article.update_by_ap_object(self.identity, None, ap_new, post)
        assert result is not None
        assert result.title == "Updated Title"
        assert result.body == "_changed_"

    def test_round_trip_from_local_ap_object(self):
        """Author a local article, serialize to AP, reimport on a fresh
        post — the resulting remote row matches the source body and tags."""
        local = Article.update_local_article(
            owner=self.identity,
            title="Round Trip",
            body="**Mark** _down_",
            tags=["alpha", "beta"],
            visibility=0,
        )
        ap = local.ap_object
        # Drop the local article so the remote create is unambiguous.
        local.delete()
        post = _make_remote_post(self.identity.pk, post_id=99099)
        result = Article.update_by_ap_object(self.identity, None, ap, post)
        assert result is not None
        assert result.local is False
        assert result.title == "Round Trip"
        assert result.body == "**Mark** _down_"
        assert sorted(result.normalized_tags) == ["alpha", "beta"]


@pytest.mark.django_db(databases="__all__")
class TestArticleRetrieveView:
    """``/article/<uuid>`` serves HTML to every Accept header; AP clients
    reach the canonical Takahe Post via the ``<link rel="alternate">`` in
    the rendered HTML head, mirroring ``review_retrieve``."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art_view@test.com", username="art_view")
        self.identity = self.user.identity
        self.client = Client()

    def test_html_renders_article(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Hello View",
            body="**bold**",
            visibility=0,
        )
        resp = self.client.get(article.url)
        assert resp.status_code == 200
        assert b"Hello View" in resp.content
        assert b"<strong>bold</strong>" in resp.content

    def test_ap_request_serves_html_with_alternate_link(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="AP Alt",
            body="Body",
            visibility=0,
        )
        resp = self.client.get(article.url, HTTP_ACCEPT="application/activity+json")
        assert resp.status_code == 200
        post = article.latest_post
        assert post is not None
        body = resp.content.decode()
        # The HTML head's ``<link rel="alternate" type="application/activity+json">``
        # points at the Takahe Post's AP ``id`` (long ``@user@domain`` form)
        # so AP clients dereference the canonical wire object on the second
        # fetch — Mastodon's ``FetchResourceService.process_html`` follows
        # this link when content negotiation returns HTML.
        assert 'rel="alternate"' in body
        assert 'type="application/activity+json"' in body
        assert post.object_uri in body


@pytest.mark.django_db(databases="__all__")
class TestArticleApIdRegression:
    """Article.ap_object must NOT carry its own ``id``: Takahe deep-merges
    ``type_data["object"]`` over the post's AP envelope, and any ``id``
    here clobbers the canonical ``object_uri`` that ``Post.by_object_uri``
    resolves remote interactions through."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art_id@test.com", username="art_id")
        self.identity = self.user.identity

    def test_ap_object_has_no_id_field(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="No id",
            body="body",
            visibility=0,
        )
        obj = article.ap_object
        assert "id" not in obj
        assert obj["href"] == article.absolute_url

    def test_post_type_data_does_not_override_id(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="Wire id",
            body="body",
            visibility=0,
        )
        post = Takahe.get_post(article.latest_post_id)
        assert post is not None
        # The merged object surfaces article fields (name, source, etc)
        # but the canonical id stays on the wrapping post.
        assert post.type_data["object"].get("id") in (None, post.object_uri)
        assert post.object_uri  # always set on local posts


@pytest.mark.django_db(databases="__all__")
class TestArticleEditedTimeRegression:
    """``edited_time`` must NOT be ``auto_now``: inbound federation copies
    the upstream ``updated`` timestamp here and the staleness guard in
    ``update_by_ap_object`` compares against it."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art_et@test.com", username="art_et")
        self.identity = self.user.identity

    def test_inbound_edited_time_preserved(self):
        post = MagicMock()
        post.local = False
        post.visibility = 0
        post.id = 88810
        post.author_id = self.identity.pk
        post.language = "en"
        post.attachments.all.return_value = []
        upstream_published = (timezone.now() - timedelta(days=2)).isoformat()
        upstream_updated = (timezone.now() - timedelta(days=1)).isoformat()
        ap = {
            "id": "https://remote.example/article/et",
            "type": "Article",
            "name": "Upstream",
            "source": {"content": "body", "mediaType": "text/markdown"},
            "published": upstream_published,
            "updated": upstream_updated,
            "attributedTo": self.identity.actor_uri,
        }
        article = Article.update_by_ap_object(self.identity, None, ap, post)
        assert article is not None
        # Refresh from DB so we see what was actually persisted, not
        # the in-memory value that was set before save.
        article.refresh_from_db()
        assert article.edited_time.isoformat() == upstream_updated


@pytest.mark.django_db(databases="__all__")
class TestArticleSearchRender:
    """``type:article`` queries must surface article hits, even though
    they have no ``item_id`` (which JournalSearchResult.items strips)."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art_srch@test.com", username="art_srch")
        self.identity = self.user.identity
        self.client = Client()
        self.client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")

    def test_article_search_view_does_not_crash(self):
        """``type:article`` queries must reach the article-aware code
        path in the view without 500-ing. The actual hit-list is
        rendered by Typesense which is environment-flaky here; the
        important regression to guard is the import + context wiring."""
        Article.update_local_article(
            owner=self.identity,
            title="Findable",
            body="searchable body",
            visibility=0,
        )
        resp = self.client.get("/search?q=type:article")
        assert resp.status_code == 200

    def test_article_search_template_renders_pieces(self):
        """Direct template render: when ``articles`` is in context, they
        must surface even with an empty ``items`` list (the bug Codex
        flagged was the template only iterating items)."""
        article = Article.update_local_article(
            owner=self.identity,
            title="Direct Render",
            body="body",
            visibility=0,
        )
        html = render_to_string(
            "search_journal.html",
            {"items": [], "articles": [article], "request": None},
        )
        assert "Direct Render" in html
        assert article.url in html


@pytest.mark.django_db(databases="__all__")
class TestArticleDeleteCascade:
    """``post_deleted`` cascades to local Article rows when the timeline
    post is removed (e.g. via Mastodon API ``DELETE /api/v1/statuses/:id``)."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art_del@test.com", username="art_del")
        self.identity = self.user.identity

    def test_local_article_row_is_deleted_when_post_is(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="To Cascade",
            body="bye",
            visibility=0,
        )
        post_id = article.latest_post_id
        assert post_id is not None
        article_pk = article.pk
        # Simulate the post-delete signal (as fired after Mastodon-API delete).
        post_deleted(post_id, True, None)
        assert not Article.objects.filter(pk=article_pk).exists()


@pytest.mark.django_db(databases="__all__")
class TestArticleFeed:
    """``/users/<handle>/feed/articles/`` serves an RSS feed of the user's
    public articles, mirroring ``ReviewFeed``."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art_feed@test.com", username="art_feed")
        self.identity = self.user.identity
        self.client = Client()
        self.feed_url = f"{self.identity.url}feed/articles/"

    def test_feed_renders_public_articles(self):
        older = Article.update_local_article(
            owner=self.identity,
            title="Older Public",
            body="**bold** body",
            tags=["alpha"],
            visibility=0,
        )
        newer = Article.update_local_article(
            owner=self.identity,
            title="Newer Public",
            body="newer body",
            visibility=0,
        )
        newer.created_time = older.created_time + timedelta(hours=1)
        newer.save(update_fields=["created_time"], post_when_save=False)
        resp = self.client.get(self.feed_url)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("application/rss+xml")
        body = resp.content.decode()
        assert "Older Public" in body
        assert "Newer Public" in body
        # markdown body rendered to HTML (escaped inside the description CDATA-less RSS)
        assert "&lt;strong&gt;bold&lt;/strong&gt;" in body
        # newest first
        assert body.index("Newer Public") < body.index("Older Public")
        # tags emitted as categories
        assert "<category>alpha</category>" in body

    def test_feed_excludes_non_public_articles(self):
        Article.update_local_article(
            owner=self.identity,
            title="Followers Only",
            body="hidden",
            visibility=1,
        )
        Article.update_local_article(
            owner=self.identity,
            title="Self Only",
            body="hidden",
            visibility=2,
        )
        resp = self.client.get(self.feed_url)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Followers Only" not in body
        assert "Self Only" not in body

    def test_feed_empty_when_not_anonymous_viewable(self):
        Article.update_local_article(
            owner=self.identity,
            title="Public But Hidden Profile",
            body="body",
            visibility=0,
        )
        self.identity.anonymous_viewable = False
        self.identity.save(update_fields=["anonymous_viewable"])
        resp = self.client.get(self.feed_url)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Public But Hidden Profile" not in body

    def test_feed_marks_sensitive_articles(self):
        Article.update_local_article(
            owner=self.identity,
            title="Touchy Subject",
            body="body",
            sensitive=True,
            visibility=0,
        )
        resp = self.client.get(self.feed_url)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Touchy Subject (may contain sensitive content)" in body
