from datetime import timedelta
from types import SimpleNamespace

import pytest

from catalog.models import Edition, ExternalResource, TVSeason, TVShow
from journal.models import Mark, Rating, Review, ShelfMember, ShelfType, Tag
from journal.models.atproto import MARK_NSID, REVIEW_NSID, build_subject
from mastodon.models import BlueskyAccount
from users.models import User


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
