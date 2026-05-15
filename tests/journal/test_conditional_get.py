"""HTTP conditional-GET behavior on anonymous piece-view endpoints.

The four piece-detail views (`post_view`, `article_retrieve`,
`review_retrieve`, `collection_retrieve`) emit ``Last-Modified`` for
anonymous viewers so re-fetches (browser soft-reload, crawler re-crawl)
short-circuit to 304 before the heavier view body runs.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from django.utils.http import http_date

from catalog.models import Edition
from journal.models import Article, Collection, Review
from users.models import User


def _hdate(dt):
    return http_date(dt.timestamp())


@pytest.mark.django_db(databases="__all__")
class TestArticleConditionalGet:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="art_cg@test.com", username="art_cg")
        self.client = Client()
        self.article = Article.update_local_article(
            owner=self.user.identity,
            title="CG Article",
            body="body",
            visibility=0,
        )

    def test_first_get_sets_last_modified_and_cache_headers(self):
        resp = self.client.get(self.article.url)
        assert resp.status_code == 200
        assert "Last-Modified" in resp
        cc = resp["Cache-Control"]
        assert "private" in cc and "must-revalidate" in cc and "max-age=0" in cc
        vary = resp["Vary"].lower()
        assert "cookie" in vary and "accept" in vary

    def test_repeat_with_if_modified_since_returns_304(self):
        first = self.client.get(self.article.url)
        last_mod = first["Last-Modified"]
        resp = self.client.get(self.article.url, HTTP_IF_MODIFIED_SINCE=last_mod)
        assert resp.status_code == 304

    def test_stale_if_modified_since_returns_200(self):
        stale = _hdate(timezone.now() - timedelta(days=1))
        resp = self.client.get(self.article.url, HTTP_IF_MODIFIED_SINCE=stale)
        assert resp.status_code == 200

    def test_authenticated_request_skips_conditional_path(self):
        # Auth users don't get Last-Modified (callback returns None for them),
        # so a 304 attempt from a logged-in client cannot succeed.
        first = self.client.get(self.article.url)
        last_mod = first["Last-Modified"]
        self.client.force_login(self.user)
        resp = self.client.get(self.article.url, HTTP_IF_MODIFIED_SINCE=last_mod)
        assert resp.status_code == 200


@pytest.mark.django_db(databases="__all__")
class TestReviewConditionalGet:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="rv_cg@test.com", username="rv_cg")
        self.book = Edition.objects.create(title="CG Book")
        self.review = Review.update_item_review(
            self.book,
            self.user.identity,
            "Title",
            "body",
            visibility=0,
        )
        self.client = Client()

    def test_repeat_returns_304(self):
        first = self.client.get(self.review.url)
        assert first.status_code == 200
        resp = self.client.get(
            self.review.url, HTTP_IF_MODIFIED_SINCE=first["Last-Modified"]
        )
        assert resp.status_code == 304


@pytest.mark.django_db(databases="__all__")
class TestCollectionConditionalGet:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="col_cg@test.com", username="col_cg")
        self.collection = Collection(
            owner=self.user.identity,
            title="CG Collection",
            brief="brief",
            visibility=0,
        )
        self.collection.save()
        self.client = Client()

    def test_repeat_returns_304(self):
        first = self.client.get(self.collection.url)
        assert first.status_code == 200
        resp = self.client.get(
            self.collection.url, HTTP_IF_MODIFIED_SINCE=first["Last-Modified"]
        )
        assert resp.status_code == 304

    def test_ap_request_skips_conditional_path(self):
        # AP requests should always run the canonical envelope code, never 304.
        first = self.client.get(
            self.collection.url, HTTP_ACCEPT="application/activity+json"
        )
        # AP path doesn't set Last-Modified.
        assert "Last-Modified" not in first


@pytest.mark.django_db(databases="__all__")
class TestPostConditionalGet:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="post_cg@test.com", username="post_cg")
        # Use a Review (which post_when_save=True) to materialize a takahē Post.
        self.book = Edition.objects.create(title="Post CG Book")
        self.review = Review.update_item_review(
            self.book,
            self.user.identity,
            "Post Title",
            "post body",
            visibility=0,
        )
        self.post = self.review.latest_post
        self.client = Client()

    def test_repeat_returns_304(self):
        assert self.post is not None
        handle = self.user.identity.full_handle
        url = f"/@{handle}/posts/{self.post.pk}/"
        first = self.client.get(url)
        assert first.status_code == 200
        resp = self.client.get(url, HTTP_IF_MODIFIED_SINCE=first["Last-Modified"])
        assert resp.status_code == 304


@pytest.mark.django_db(databases="__all__")
class TestCollectionMemberEditBumpsEditedTime:
    """Member edits — append, remove, reorder, metadata, view-level note —
    must bump ``Collection.edited_time`` so that:
      * conditional-GET mtime reflects the change
      * the federated re-post path (post_when_save) re-fires
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="bump@test.com", username="bump")
        self.book1 = Edition.objects.create(title="Bump Book 1")
        self.book2 = Edition.objects.create(title="Bump Book 2")
        self.collection = Collection(
            owner=self.user.identity, title="Bump", brief="brief", visibility=0
        )
        self.collection.save()

    def _refetch_edited_time(self):
        return Collection.objects.values_list("edited_time", flat=True).get(
            pk=self.collection.pk
        )

    def test_append_bumps_edited_time(self):
        before = self._refetch_edited_time()
        self.collection.append_item(self.book1)
        assert self._refetch_edited_time() > before

    def test_remove_bumps_edited_time(self):
        self.collection.append_item(self.book1)
        before = self._refetch_edited_time()
        self.collection.remove_item(self.book1)
        assert self._refetch_edited_time() > before

    def test_reorder_bumps_edited_time(self):
        m1, _ = self.collection.append_item(self.book1)
        m2, _ = self.collection.append_item(self.book2)
        before = self._refetch_edited_time()
        self.collection.update_member_order([m2.pk, m1.pk])
        assert self._refetch_edited_time() > before

    def test_update_item_metadata_bumps_edited_time(self):
        self.collection.append_item(self.book1)
        before = self._refetch_edited_time()
        self.collection.update_item_metadata(self.book1, {"note": "new note"})
        assert self._refetch_edited_time() > before

    def test_view_update_item_note_bumps_edited_time(self):
        self.collection.append_item(self.book1, note="old")
        before = self._refetch_edited_time()
        client = Client()
        client.force_login(self.user)
        resp = client.post(
            f"/collection/{self.collection.uuid}/update_item_note/{self.book1.uuid}",
            data={"note": "fresh note"},
        )
        assert resp.status_code == 200
        assert self._refetch_edited_time() > before


@pytest.mark.django_db(databases="__all__")
class TestConditionalGetGating:
    """Privacy / handle / dynamic-collection gates must short-circuit the
    304 path so a cached anonymous 200 doesn't outlive a privacy flip or
    cover a bogus URL."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="gate@test.com", username="gate")
        self.client = Client()

    def test_article_anonymous_viewable_flip_invalidates_304(self):
        article = Article.update_local_article(
            owner=self.user.identity,
            title="Flip",
            body="body",
            visibility=0,
        )
        first = self.client.get(article.url)
        assert first.status_code == 200
        last_mod = first["Last-Modified"]
        # Owner-level privacy toggle — doesn't bump article.edited_time.
        self.user.identity.anonymous_viewable = False
        self.user.identity.save()
        resp = self.client.get(article.url, HTTP_IF_MODIFIED_SINCE=last_mod)
        # Must NOT 304; the view body should run and return 403.
        assert resp.status_code != 304

    def test_review_anonymous_viewable_flip_invalidates_304(self):
        book = Edition.objects.create(title="Gate Book")
        review = Review.update_item_review(
            book, self.user.identity, "Title", "body", visibility=0
        )
        first = self.client.get(review.url)
        assert first.status_code == 200
        last_mod = first["Last-Modified"]
        self.user.identity.anonymous_viewable = False
        self.user.identity.save()
        resp = self.client.get(review.url, HTTP_IF_MODIFIED_SINCE=last_mod)
        assert resp.status_code != 304

    def test_collection_anonymous_viewable_flip_invalidates_304(self):
        col = Collection(
            owner=self.user.identity, title="Flip Col", brief="x", visibility=0
        )
        col.save()
        first = self.client.get(col.url)
        assert first.status_code == 200
        last_mod = first["Last-Modified"]
        self.user.identity.anonymous_viewable = False
        self.user.identity.save()
        resp = self.client.get(col.url, HTTP_IF_MODIFIED_SINCE=last_mod)
        assert resp.status_code != 304

    def test_post_wrong_handle_does_not_304(self):
        book = Edition.objects.create(title="Post Gate Book")
        review = Review.update_item_review(
            book, self.user.identity, "Title", "body", visibility=0
        )
        post = review.latest_post
        assert post is not None
        good_url = f"/@gate/posts/{post.pk}/"
        first = self.client.get(good_url)
        assert first.status_code == 200
        last_mod = first["Last-Modified"]
        # Wrong handle for the same post pk: must 404, never 304.
        bad_url = f"/@nobody/posts/{post.pk}/"
        resp = self.client.get(bad_url, HTTP_IF_MODIFIED_SINCE=last_mod)
        assert resp.status_code != 304

    def test_article_ap_accept_skips_conditional_path(self):
        # AP requests on the article URL should redirect to the canonical
        # Post object_uri, not 304-bypass via the article's mtime.
        article = Article.update_local_article(
            owner=self.user.identity,
            title="Alt",
            body="b",
            visibility=0,
        )
        first = self.client.get(article.url)
        assert first.status_code == 200
        last_mod = first["Last-Modified"]
        resp = self.client.get(
            article.url,
            HTTP_ACCEPT="application/activity+json",
            HTTP_IF_MODIFIED_SINCE=last_mod,
        )
        # Must NOT be 304; the AP-Accept path runs the view and redirects.
        assert resp.status_code != 304

    def test_post_view_rejects_json_accept(self):
        # Regression for the ``HTTP_ACCEPT`` vs ``Accept`` header-name bug —
        # ``request.headers`` keys are normalized; ``HTTP_ACCEPT`` always
        # returned None and silently let JSON requests through.
        book = Edition.objects.create(title="Hdr Book")
        review = Review.update_item_review(
            book, self.user.identity, "T", "b", visibility=0
        )
        post = review.latest_post
        assert post is not None
        resp = self.client.get(
            f"/@gate/posts/{post.pk}/",
            HTTP_ACCEPT="application/activity+json",
        )
        # The view's JSON-Accept guard should fire (400 BadRequest).
        assert resp.status_code == 400

    def test_dynamic_collection_does_not_304(self):
        col = Collection(
            owner=self.user.identity,
            title="Dyn",
            brief="x",
            visibility=0,
            query="type:book",
        )
        col.save()
        first = self.client.get(col.url)
        assert first.status_code == 200
        # Dynamic collections must not emit Last-Modified — their member
        # set drifts independently of ``edited_time``.
        assert "Last-Modified" not in first

    def test_304_response_carries_cache_control_and_vary(self):
        article = Article.update_local_article(
            owner=self.user.identity,
            title="Hdr",
            body="b",
            visibility=0,
        )
        first = self.client.get(article.url)
        resp = self.client.get(
            article.url, HTTP_IF_MODIFIED_SINCE=first["Last-Modified"]
        )
        assert resp.status_code == 304
        cc = resp.get("Cache-Control", "")
        assert "must-revalidate" in cc and "private" in cc
        vary = resp.get("Vary", "").lower()
        assert "cookie" in vary and "accept" in vary


@pytest.mark.django_db(databases="__all__")
class TestAlternateLinkInHead:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="alt_link@test.com", username="alt_link")
        self.client = Client()

    def test_article_has_ap_alternate_link(self):
        article = Article.update_local_article(
            owner=self.user.identity,
            title="Alt Article",
            body="body",
            visibility=0,
        )
        resp = self.client.get(article.url)
        post = article.latest_post
        assert post is not None
        ap = post.absolute_object_uri()
        body = resp.content.decode()
        assert 'rel="alternate"' in body
        assert 'type="application/activity+json"' in body
        assert ap in body
