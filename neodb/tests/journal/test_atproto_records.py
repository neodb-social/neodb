from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.conf import settings

from catalog.models import Edition, ExternalResource, TVSeason, TVShow
from journal.models import Article, Mark, Rating, Review, ShelfMember, ShelfType, Tag
from journal.models.atproto import (
    DOCUMENT_NSID,
    MARK_NSID,
    MARKPUB_MARKDOWN_NSID,
    MARKPUB_TEXT_NSID,
    REVIEW_NSID,
    build_document_rkey,
    build_subject,
)
from mastodon.models import BlueskyAccount
from users.models import User

_TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"


class FakeBluesky:
    """Stand-in for BlueskyAccount capturing record writes/deletes."""

    def __init__(self):
        self.uid = "did:plc:fake"
        self.puts: dict[tuple[str, str], dict] = {}
        self.deletes: list[tuple[str, str]] = []

    def put_record(self, collection, rkey, record):
        self.puts[(collection, rkey)] = record
        return {"uri": f"at://{self.uid}/{collection}/{rkey}", "cid": "cid"}

    def delete_record(self, collection, rkey):
        self.deletes.append((collection, rkey))


@pytest.mark.django_db(databases="__all__")
def test_review_atproto_record():
    user = User.register(email="rev@example.com", username="revuser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(
        book, user.identity, "Loved it", "A **great** read."
    )
    assert review is not None
    records = review.to_atproto_records()
    assert len(records) == 1
    collection, record = records[0]
    assert collection == REVIEW_NSID
    assert record["$type"] == REVIEW_NSID
    assert record["title"] == "Loved it"
    assert record["body"] == "A **great** read."
    assert record["subject"]["uri"] == book.absolute_url
    assert record["subject"]["category"] == "book"
    assert record["subject"]["type"] == "Edition"
    assert record["subject"]["title"] == "Dune"
    assert record["createdAt"].endswith("Z")
    assert review.atproto_collections() == {REVIEW_NSID}


@pytest.mark.django_db(databases="__all__")
def test_review_updated_at_only_for_real_edits():
    user = User.register(email="upd@example.com", username="upduser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None

    _, record = review.to_atproto_records()[0]
    # creation jitter between created_time and edited_time is not an edit
    assert "updatedAt" not in record

    Review.objects.filter(pk=review.pk).update(
        edited_time=review.created_time + timedelta(minutes=5)
    )
    review.refresh_from_db()
    _, record = review.to_atproto_records()[0]
    assert record["updatedAt"].endswith("Z")


@pytest.mark.django_db(databases="__all__")
def test_subject_differentiates_tv_types_and_uses_source_urls():
    show = TVShow.objects.create(title="The Show")
    season = TVSeason.objects.create(title="Season 1")
    ExternalResource.objects.create(
        item=season,
        id_type="tmdb_tvseason",
        id_value="100-1",
        url="https://www.themoviedb.org/tv/100/season/1",
    )
    ExternalResource.objects.create(
        item=season,
        id_type="imdb",
        id_value="tt100",
        url="https://www.imdb.com/title/tt100/",
    )

    show_subject = build_subject(show)
    season_subject = build_subject(season)

    # broad category is shared; specific type differentiates them
    assert show_subject["category"] == season_subject["category"] == "tv"
    assert show_subject["type"] == "TVShow"
    assert season_subject["type"] == "TVSeason"
    # external sources are referenced by URL, not raw id
    assert season_subject["sources"] == [
        "https://www.imdb.com/title/tt100/",
        "https://www.themoviedb.org/tv/100/season/1",
    ]
    assert "sources" not in show_subject
    # only standardized (IdealIdTypes) identifiers are listed:
    # imdb qualifies, the site-specific tmdb season id does not
    assert season_subject["identifiers"] == [{"type": "imdb", "value": "tt100"}]
    assert "identifiers" not in show_subject


@pytest.mark.django_db(databases="__all__")
def test_mark_atproto_record_embeds_rating():
    user = User.register(email="mark@example.com", username="markuser")
    book = Edition.objects.create(title="Dune")
    Mark(user.identity, book).update(
        ShelfType.COMPLETE, "finished", 9, tags=["scifi"], visibility=0
    )
    sm = ShelfMember.objects.get(owner=user.identity, item=book)
    records = sm.to_atproto_records()
    assert len(records) == 1
    collection, record = records[0]
    assert collection == MARK_NSID
    assert record["status"] == "complete"
    assert record["comment"] == "finished"
    assert record["rating"] == {"value": 9, "max": 10}
    assert record["tags"] == ["scifi"]
    assert sm.atproto_collections() == {MARK_NSID}


@pytest.mark.django_db(databases="__all__")
def test_review_record_keyed_by_review_uuid():
    user = User.register(email="rkey@example.com", username="rkeyuser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "Title", "body")
    assert review is not None

    fake = FakeBluesky()
    review._sync_records_to_bluesky(fake)

    # keyed by the review's own uuid so multiple reviews per work can coexist
    assert (REVIEW_NSID, review.uuid) in fake.puts
    assert (REVIEW_NSID, book.uuid) not in fake.puts


@pytest.mark.django_db(databases="__all__")
def test_sync_writes_record_keyed_by_piece_uuid():
    user = User.register(email="sync@example.com", username="syncuser")
    book = Edition.objects.create(title="Dune")
    Mark(user.identity, book).update(ShelfType.COMPLETE, "great", 7, visibility=0)
    sm = ShelfMember.objects.get(owner=user.identity, item=book)

    fake = FakeBluesky()
    sm._sync_records_to_bluesky(fake)

    # keyed by the mark's own uuid, stable across item merges
    assert (MARK_NSID, sm.uuid) in fake.puts
    assert (MARK_NSID, book.uuid) not in fake.puts
    assert fake.deletes == []


@pytest.mark.django_db(databases="__all__")
def test_sync_drops_embedded_rating_when_rating_removed():
    user = User.register(email="rm@example.com", username="rmuser")
    book = Edition.objects.create(title="Dune")
    Mark(user.identity, book).update(ShelfType.COMPLETE, "great", 7, visibility=0)
    # remove the rating directly (mark.update treats rating_grade=None as no-op)
    Rating.update_item_rating(book, user.identity, None)
    sm = ShelfMember.objects.get(owner=user.identity, item=book)

    fake = FakeBluesky()
    sm._sync_records_to_bluesky(fake)

    record = fake.puts[(MARK_NSID, sm.uuid)]
    assert "rating" not in record
    assert fake.deletes == []


@pytest.mark.django_db(databases="__all__")
def test_sync_drop_deletes_record():
    user = User.register(email="drop@example.com", username="dropuser")
    book = Edition.objects.create(title="Dune")
    Mark(user.identity, book).update(ShelfType.COMPLETE, "great", 7, visibility=0)
    sm = ShelfMember.objects.get(owner=user.identity, item=book)

    fake = FakeBluesky()
    sm._sync_records_to_bluesky(fake, drop=True)

    assert fake.puts == {}
    assert (MARK_NSID, sm.uuid) in fake.deletes


@pytest.mark.django_db(databases="__all__")
def test_mark_record_excludes_private_tags():
    user = User.register(email="tags@example.com", username="taguser")
    book = Edition.objects.create(title="Dune")
    Mark(user.identity, book).update(
        ShelfType.COMPLETE, None, None, tags=["pub", "secret"], visibility=0
    )
    Tag.objects.filter(owner=user.identity, title="secret").update(visibility=2)
    sm = ShelfMember.objects.get(owner=user.identity, item=book)

    records = dict(sm.to_atproto_records())

    assert records[MARK_NSID]["tags"] == ["pub"]


@pytest.mark.django_db(databases="__all__")
def test_review_record_includes_fediverse_uri_when_post_linked():
    user = User.register(email="fed@example.com", username="feduser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None
    # simulate a linked timeline post (populates the latest_post cache)
    review.__dict__["latest_post"] = SimpleNamespace(
        object_uri="https://nd.test/@feduser/posts/1/"
    )

    _, record = review.to_atproto_records()[0]

    # back-reference to the originating fediverse post
    assert record["fediverseUri"] == "https://nd.test/@feduser/posts/1/"


@pytest.mark.django_db(databases="__all__")
def test_mark_record_includes_fediverse_uri_when_post_linked():
    user = User.register(email="fedmark@example.com", username="fedmarkuser")
    book = Edition.objects.create(title="Dune")
    Mark(user.identity, book).update(ShelfType.COMPLETE, "done", 8, visibility=0)
    sm = ShelfMember.objects.get(owner=user.identity, item=book)
    sm.__dict__["latest_post"] = SimpleNamespace(
        object_uri="https://nd.test/@fedmarkuser/posts/2/"
    )

    _, record = sm.to_atproto_records()[0]

    assert record["fediverseUri"] == "https://nd.test/@fedmarkuser/posts/2/"


@pytest.mark.django_db(databases="__all__")
def test_record_omits_fediverse_uri_without_post():
    user = User.register(email="nofed@example.com", username="nofeduser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None

    # with no linked timeline post the field is simply omitted
    review.__dict__["latest_post"] = None  # force the no-post case
    _, record = review.to_atproto_records()[0]
    assert "fediverseUri" not in record


@pytest.mark.django_db(databases="__all__")
def test_mark_record_omits_fediverse_uri_without_post():
    user = User.register(email="nofedmark@example.com", username="nofedmarkuser")
    book = Edition.objects.create(title="Dune")
    Mark(user.identity, book).update(ShelfType.COMPLETE, "c", 5, visibility=0)
    sm = ShelfMember.objects.get(owner=user.identity, item=book)

    sm.__dict__["latest_post"] = None  # force the no-post case
    _, record = sm.to_atproto_records()[0]
    assert "fediverseUri" not in record


@pytest.mark.django_db(databases="__all__")
def test_delete_enqueues_record_cleanup_without_metadata(monkeypatch):
    user = User.register(email="del@example.com", username="deluser")
    book = Edition.objects.create(title="Dune")
    Mark(user.identity, book).update(ShelfType.COMPLETE, None, None, visibility=0)
    BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:fake", handle="del.example"
    )
    sm = ShelfMember.objects.get(owner=user.identity, item=book)
    assert not sm.metadata  # no skeet was ever posted

    calls = []
    monkeypatch.setattr(
        "journal.models.common.django_rq.get_queue",
        lambda name: SimpleNamespace(enqueue=lambda *a, **kw: calls.append(a)),
    )
    sm.delete_crossposts()

    # cleanup must be enqueued even though crosspost metadata is empty
    assert len(calls) == 1
    _func, _user_id, metadata, record_refs = calls[0]
    assert metadata == {}
    assert record_refs == [[MARK_NSID, sm.uuid]]


@pytest.mark.django_db(databases="__all__")
def test_review_atproto_document():
    user = User.register(email="doc@example.com", username="docuser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(
        book, user.identity, "Loved it", "A **great** read."
    )
    assert review is not None

    doc = review.to_atproto_document()

    assert doc["$type"] == DOCUMENT_NSID
    # loose document: site + path reconstructs the canonical NeoDB URL
    assert doc["site"] == settings.SITE_INFO["site_url"].rstrip("/")
    assert doc["site"] + doc["path"] == review.absolute_url
    assert doc["title"] == "Loved it"
    assert doc["publishedAt"].endswith("Z")
    assert "updatedAt" not in doc  # creation jitter is not an edit
    # full markdown in the open content union, plaintext alongside
    assert doc["content"]["$type"] == MARKPUB_MARKDOWN_NSID
    assert doc["content"]["text"]["$type"] == MARKPUB_TEXT_NSID
    assert doc["content"]["text"]["markdown"] == "A **great** read."
    assert " ".join(doc["textContent"].split()) == "A great read."
    # spoiler-safe auto-summary, not a body excerpt
    assert doc["description"] == review.display_summary
    assert review.atproto_document_collections() == {DOCUMENT_NSID}


@pytest.mark.django_db(databases="__all__")
def test_article_atproto_document():
    user = User.register(email="artdoc@example.com", username="artdocuser")
    article = Article.update_local_article(
        user.identity,
        "My Essay",
        "Some **bold** thoughts.",
        tags=["essay", "life"],
    )

    doc = article.to_atproto_document()

    assert doc["$type"] == DOCUMENT_NSID
    assert doc["site"] + doc["path"] == article.absolute_url
    assert doc["title"] == "My Essay"
    assert doc["content"]["text"]["markdown"] == "Some **bold** thoughts."
    assert doc["tags"] == ["essay", "life"]
    # no author summary: description falls back to the body excerpt
    assert doc["description"] == article.excerpt
    assert article.atproto_document_collections() == {DOCUMENT_NSID}


@pytest.mark.django_db(databases="__all__")
def test_article_document_description_prefers_summary():
    user = User.register(email="artsum@example.com", username="artsumuser")
    article = Article.update_local_article(
        user.identity, "T", "body", summary="hand-written teaser"
    )

    doc = article.to_atproto_document()

    assert doc["description"] == "hand-written teaser"


@pytest.mark.django_db(databases="__all__")
def test_document_rkey_is_valid_tid():
    user = User.register(email="tid@example.com", username="tiduser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None
    article = Article.update_local_article(user.identity, "T", "body")

    for piece in (review, article):
        rkey = build_document_rkey(piece)
        # the site.standard.document lexicon requires tid record keys
        assert len(rkey) == 13
        assert all(c in _TID_ALPHABET for c in rkey)
        assert rkey[0] in "234567abcdefghij"  # top bit of a TID is 0
        assert build_document_rkey(piece) == rkey  # deterministic
    assert build_document_rkey(review) != build_document_rkey(article)


def test_document_rkey_unique_for_equal_created_time():
    # date-only backdated imports land many pieces on the same microsecond;
    # keys must still be unique per piece, even for pks 1024 apart (sharing
    # the low clock-id bits) and for pre-1970 times (clamped, not wrapped)
    for dt in (
        datetime(2020, 5, 1, tzinfo=dt_timezone.utc),
        datetime(1932, 1, 1, tzinfo=dt_timezone.utc),
    ):
        keys = [
            build_document_rkey(Review(pk=pk, created_time=dt))
            for pk in (1, 2, 1025, 2049, 1024 * 1024 + 1)
        ]
        assert len(set(keys)) == len(keys)
        for rkey in keys:
            assert len(rkey) == 13
            assert all(c in _TID_ALPHABET for c in rkey)


@pytest.mark.django_db(databases="__all__")
def test_sync_writes_document_and_freezes_rkey():
    user = User.register(email="freeze@example.com", username="freezeuser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None

    fake = FakeBluesky()
    review._sync_records_to_bluesky(fake)

    rkey = build_document_rkey(review)
    assert (DOCUMENT_NSID, rkey) in fake.puts
    # both the structured review record and the document are written
    assert (REVIEW_NSID, review.uuid) in fake.puts
    # the key is frozen so a later created_time edit cannot orphan the record
    assert review.metadata["atproto_document_rkey"] == rkey
    review.created_time = review.created_time - timedelta(days=30)
    assert review.atproto_document_rkey() == rkey
    review._sync_records_to_bluesky(fake)
    assert len([k for k in fake.puts if k[0] == DOCUMENT_NSID]) == 1


@pytest.mark.django_db(databases="__all__")
def test_sync_drop_deletes_document():
    user = User.register(email="dropdoc@example.com", username="dropdocuser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None
    fake = FakeBluesky()
    review._sync_records_to_bluesky(fake)
    rkey = review.metadata["atproto_document_rkey"]

    review._sync_records_to_bluesky(fake, drop=True)

    assert (DOCUMENT_NSID, rkey) in fake.deletes
    assert (REVIEW_NSID, review.uuid) in fake.deletes
    assert "atproto_document_rkey" not in review.metadata


@pytest.mark.django_db(databases="__all__")
def test_document_includes_bsky_post_ref():
    user = User.register(email="ref@example.com", username="refuser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None

    doc = review.to_atproto_document()
    assert "bskyPostRef" not in doc  # no skeet was posted

    review.metadata.update(
        {"bluesky_id": "at://did:plc:fake/app.bsky.feed.post/3k", "bluesky_cid": "c1"}
    )
    doc = review.to_atproto_document()
    assert doc["bskyPostRef"] == {
        "uri": "at://did:plc:fake/app.bsky.feed.post/3k",
        "cid": "c1",
    }


@pytest.mark.django_db(databases="__all__")
def test_delete_enqueues_document_cleanup(monkeypatch):
    user = User.register(email="deldoc@example.com", username="deldocuser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None
    BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:fake", handle="deldoc.example"
    )

    calls = []
    monkeypatch.setattr(
        "journal.models.common.django_rq.get_queue",
        lambda name: SimpleNamespace(enqueue=lambda *a, **kw: calls.append(a)),
    )
    review.delete_crossposts()

    assert len(calls) == 1
    _func, _user_id, _metadata, record_refs = calls[0]
    assert [REVIEW_NSID, review.uuid] in record_refs
    assert [DOCUMENT_NSID, build_document_rkey(review)] in record_refs


@pytest.mark.django_db(databases="__all__")
def test_article_sync_and_drop_document():
    user = User.register(email="artsync@example.com", username="artsyncuser")
    article = Article.update_local_article(user.identity, "T", "body")

    fake = FakeBluesky()
    article._sync_records_to_bluesky(fake)

    rkey = article.metadata["atproto_document_rkey"]
    assert (DOCUMENT_NSID, rkey) in fake.puts

    article._sync_records_to_bluesky(fake, drop=True)

    assert (DOCUMENT_NSID, rkey) in fake.deletes
    assert "atproto_document_rkey" not in article.metadata


@pytest.mark.django_db(databases="__all__")
def test_failed_repost_drops_stale_bsky_post_ref(monkeypatch):
    user = User.register(email="stale@example.com", username="staleuser")
    book = Edition.objects.create(title="Dune")
    review = Review.update_item_review(book, user.identity, "T", "body")
    assert review is not None
    BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:fake", handle="stale.example"
    )
    # a previous sync posted a skeet
    Review.objects.filter(pk=review.pk).update(
        metadata={
            "bluesky_id": "at://did:plc:fake/app.bsky.feed.post/3old",
            "bluesky_cid": "oldcid",
        }
    )
    review.refresh_from_db()

    fake = FakeBluesky()
    deleted_posts = []
    monkeypatch.setattr(
        BlueskyAccount, "delete_post", lambda self, uri: deleted_posts.append(uri)
    )

    def fail_post(self, **kwargs):
        raise RuntimeError("pds down")

    monkeypatch.setattr(BlueskyAccount, "post", fail_post)
    monkeypatch.setattr(BlueskyAccount, "put_record", fake.put_record)
    monkeypatch.setattr(BlueskyAccount, "delete_record", fake.delete_record)

    review._sync_to_social_accounts(0)

    # the old skeet was deleted and reposting failed: the document must not
    # carry a bskyPostRef pointing at the deleted post
    assert deleted_posts == ["at://did:plc:fake/app.bsky.feed.post/3old"]
    doc = next(rec for (c, _), rec in fake.puts.items() if c == DOCUMENT_NSID)
    assert "bskyPostRef" not in doc
    # changes made during sync (dropped ids, frozen rkey) are persisted
    review.refresh_from_db()
    assert "bluesky_id" not in review.metadata
    assert "bluesky_cid" not in review.metadata
    assert review.metadata["atproto_document_rkey"] == build_document_rkey(review)
