"""Tests for ap_object creation/parsing in journal models.

Verifies that:
- ap_object properties return correct structure for each model type
- get_ap_data() wraps ap_object correctly for post creation
- Syncing to timeline creates correct type_data in Takahe posts
- update_by_ap_object() correctly parses ap_objects (round-trip)
"""

from unittest.mock import MagicMock

import pytest

from catalog.models import Edition
from journal.models import (
    Collection,
    Comment,
    Mark,
    Note,
    Rating,
    Review,
    ShelfMember,
    ShelfType,
)
from takahe.utils import Takahe
from users.models import User


def _make_remote_post(identity_pk: int, post_id: int = 88888) -> MagicMock:
    """Return a mock representing an incoming remote (federated) Takahe post."""
    post = MagicMock()
    post.local = False
    post.visibility = 0  # public in Takahe numeric representation
    post.id = post_id
    post.author_id = identity_pk
    post.summary = None
    post.sensitive = False
    post.attachments.all.return_value = []
    return post


@pytest.mark.django_db(databases="__all__")
class TestApObjectStructure:
    """Verify ap_object properties return the correct structure for each model."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book = Edition.objects.create(title="Test Book")
        self.user = User.register(email="apobj@test.com", username="apobj_user")
        self.identity = self.user.identity

    def test_shelf_member_ap_object(self):
        Mark(self.identity, self.book).update(ShelfType.COMPLETE, visibility=0)
        member = ShelfMember.objects.get(owner=self.identity, item=self.book)
        obj = member.ap_object
        assert obj["type"] == "Status"
        assert obj["status"] == ShelfType.COMPLETE
        assert obj["id"] == member.absolute_url
        assert obj["href"] == member.absolute_url
        assert obj["attributedTo"] == self.identity.actor_uri
        assert obj["withRegardTo"] == self.book.absolute_url
        assert "published" in obj
        assert "updated" in obj

    def test_review_ap_object(self):
        review = Review.update_item_review(
            self.book, self.identity, "Great Book", "Really enjoyed it.", visibility=0
        )
        assert review is not None
        obj = review.ap_object
        assert obj["type"] == "Review"
        assert obj["name"] == "Great Book"
        assert obj["content"] == "Really enjoyed it."
        assert obj["mediaType"] == "text/markdown"
        assert obj["id"] == review.absolute_url
        assert obj["href"] == review.absolute_url
        assert obj["attributedTo"] == self.identity.actor_uri
        assert obj["withRegardTo"] == self.book.absolute_url
        assert "published" in obj
        assert "updated" in obj

    def test_note_ap_object_without_progress(self):
        note = Note.objects.create(
            item=self.book,
            owner=self.identity,
            content="Some reading notes",
            visibility=0,
        )
        obj = note.ap_object
        assert obj["type"] == "Note"
        assert obj["content"] == "Some reading notes"
        assert obj["title"] is None
        assert obj["sensitive"] is False
        assert obj["id"] == note.absolute_url
        assert obj["href"] == note.absolute_url
        assert obj["attributedTo"] == self.identity.actor_uri
        assert obj["withRegardTo"] == self.book.absolute_url
        assert "progress" not in obj
        assert "published" in obj
        assert "updated" in obj

    def test_note_ap_object_with_progress(self):
        note = Note.objects.create(
            item=self.book,
            owner=self.identity,
            content="At chapter 5",
            progress_type=Note.ProgressType.CHAPTER,
            progress_value="5",
            visibility=0,
        )
        obj = note.ap_object
        assert obj["type"] == "Note"
        assert "progress" in obj
        assert obj["progress"]["type"] == Note.ProgressType.CHAPTER
        assert obj["progress"]["value"] == "5"

    def test_note_ap_object_with_title_and_sensitive(self):
        note = Note.objects.create(
            item=self.book,
            owner=self.identity,
            title="Spoiler warning",
            content="The ending is ...",
            sensitive=True,
            visibility=0,
        )
        obj = note.ap_object
        assert obj["title"] == "Spoiler warning"
        assert obj["sensitive"] is True

    def test_comment_ap_object_without_position(self):
        comment = Comment.comment_item(
            self.book, self.identity, "Nice read!", visibility=0
        )
        assert comment is not None
        obj = comment.ap_object
        assert obj["type"] == "Comment"
        assert obj["content"] == "Nice read!"
        assert obj["id"] == comment.absolute_url
        assert obj["href"] == comment.absolute_url
        assert obj["attributedTo"] == self.identity.actor_uri
        assert obj["withRegardTo"] == self.book.absolute_url
        assert "relatedWithItemPosition" not in obj
        assert "published" in obj
        assert "updated" in obj

    def test_comment_ap_object_with_position(self):
        comment = Comment.objects.create(
            item=self.book,
            owner=self.identity,
            text="Great scene here",
            metadata={"position": "1:23:45"},
            visibility=0,
        )
        obj = comment.ap_object
        assert obj["type"] == "Comment"
        assert obj["relatedWithItemPosition"] == "1:23:45"
        assert obj["relatedWithItemPositionType"] == "time"

    def test_rating_ap_object(self):
        Rating.update_item_rating(self.book, self.identity, 8, visibility=0)
        rating = Rating.objects.get(owner=self.identity, item=self.book)
        obj = rating.ap_object
        assert obj["type"] == "Rating"
        assert obj["value"] == 8
        assert obj["best"] == 10
        assert obj["worst"] == 1
        assert obj["id"] == rating.absolute_url
        assert obj["href"] == rating.absolute_url
        assert obj["attributedTo"] == self.identity.actor_uri
        assert obj["withRegardTo"] == self.book.absolute_url
        assert "published" in obj
        assert "updated" in obj

    def test_collection_ap_object(self):
        collection = Collection.objects.create(
            owner=self.identity,
            title="My Collection",
            brief="A brief description",
            visibility=0,
        )
        obj = collection.ap_object
        assert obj["type"] == "Collection"
        assert obj["name"] == "My Collection"
        assert obj["content"] == "A brief description"
        assert obj["mediaType"] == "text/markdown"
        assert obj["id"] == collection.absolute_url
        assert obj["href"] == collection.absolute_url
        assert obj["attributedTo"] == self.identity.actor_uri
        assert "withRegardTo" not in obj
        assert "published" in obj
        assert "updated" in obj

    def test_collection_member_ap_object(self):
        collection = Collection.objects.create(
            owner=self.identity,
            title="My Collection",
            visibility=0,
        )
        member, _ = collection.append_item(self.book, note="A member note")
        assert member is not None
        obj = member.ap_object
        assert obj["type"] == "CollectionItem"
        assert obj["collection"] == collection.absolute_url
        assert obj["note"] == "A member note"
        assert obj["id"] == member.absolute_url
        assert obj["href"] == member.absolute_url
        assert obj["attributedTo"] == self.identity.actor_uri
        assert obj["withRegardTo"] == self.book.absolute_url
        assert "published" in obj
        assert "updated" in obj


@pytest.mark.django_db(databases="__all__")
class TestGetApData:
    """Verify get_ap_data() wraps ap_object correctly for post creation."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book = Edition.objects.create(title="Test Book")
        self.user = User.register(email="getapdata@test.com", username="getapdata_user")
        self.identity = self.user.identity

    def test_shelf_member_get_ap_data_status_only(self):
        Mark(self.identity, self.book).update(ShelfType.WISHLIST, visibility=0)
        member = ShelfMember.objects.get(owner=self.identity, item=self.book)
        data = member.get_ap_data()
        assert "object" in data
        obj_data = data["object"]
        assert "tag" in obj_data
        assert "relatedWith" in obj_data
        related = obj_data["relatedWith"]
        assert len(related) == 1
        assert related[0]["type"] == "Status"
        assert related[0]["status"] == ShelfType.WISHLIST

    def test_shelf_member_get_ap_data_with_comment_and_rating(self):
        Mark(self.identity, self.book).update(
            ShelfType.COMPLETE, "Excellent book", 9, visibility=0
        )
        member = ShelfMember.objects.get(owner=self.identity, item=self.book)
        data = member.get_ap_data()
        related = data["object"]["relatedWith"]
        assert len(related) == 3
        types = {r["type"] for r in related}
        assert types == {"Status", "Comment", "Rating"}

    def test_shelf_member_get_ap_data_with_comment_no_rating(self):
        Mark(self.identity, self.book).update(
            ShelfType.PROGRESS, "Halfway through", visibility=0
        )
        member = ShelfMember.objects.get(owner=self.identity, item=self.book)
        data = member.get_ap_data()
        related = data["object"]["relatedWith"]
        assert len(related) == 2
        types = {r["type"] for r in related}
        assert types == {"Status", "Comment"}

    def test_review_get_ap_data(self):
        review = Review.update_item_review(
            self.book, self.identity, "My Title", "Body text", visibility=0
        )
        assert review is not None
        data = review.get_ap_data()
        obj_data = data["object"]
        assert "tag" in obj_data
        assert "relatedWith" in obj_data
        related = obj_data["relatedWith"]
        assert len(related) == 1
        assert related[0]["type"] == "Review"
        assert related[0]["name"] == "My Title"
        assert related[0]["content"] == "Body text"

    def test_note_get_ap_data(self):
        note = Note.objects.create(
            item=self.book,
            owner=self.identity,
            content="Reading notes",
            visibility=0,
        )
        data = note.get_ap_data()
        obj_data = data["object"]
        assert "tag" in obj_data
        related = obj_data["relatedWith"]
        assert len(related) == 1
        assert related[0]["type"] == "Note"
        assert related[0]["content"] == "Reading notes"

    def test_collection_get_ap_data(self):
        collection = Collection.objects.create(
            owner=self.identity,
            title="Test Collection",
            visibility=0,
        )
        data = collection.get_ap_data()
        obj_data = data["object"]
        # Collection.get_ap_data does not include "tag"
        assert "relatedWith" in obj_data
        related = obj_data["relatedWith"]
        assert len(related) == 1
        assert related[0]["type"] == "Collection"
        assert related[0]["name"] == "Test Collection"


@pytest.mark.django_db(databases="__all__")
class TestPostTypeData:
    """Verify that syncing to timeline stores correct type_data in the Takahe post."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book = Edition.objects.create(title="Test Book")
        self.user = User.register(email="posttd@test.com", username="posttd_user")
        self.identity = self.user.identity

    def test_shelf_member_post_type_data(self):
        Mark(self.identity, self.book).update(ShelfType.PROGRESS, visibility=0)
        mark = Mark(self.identity, self.book)
        post_id = mark.latest_post_id
        assert post_id is not None
        post = Takahe.get_post(post_id)
        assert post is not None
        assert post.type_data is not None
        related = post.type_data["object"]["relatedWith"]
        assert len(related) == 1
        assert related[0]["type"] == "Status"
        assert related[0]["status"] == ShelfType.PROGRESS

    def test_shelf_member_with_comment_rating_post_type_data(self):
        Mark(self.identity, self.book).update(
            ShelfType.COMPLETE, "My comment", 7, visibility=0
        )
        mark = Mark(self.identity, self.book)
        post_id = mark.latest_post_id
        assert post_id is not None
        post = Takahe.get_post(post_id)
        assert post is not None
        related = post.type_data["object"]["relatedWith"]
        assert len(related) == 3
        types = {r["type"] for r in related}
        assert types == {"Status", "Comment", "Rating"}
        rating_obj = next(r for r in related if r["type"] == "Rating")
        assert rating_obj["value"] == 7
        assert rating_obj["best"] == 10
        assert rating_obj["worst"] == 1
        comment_obj = next(r for r in related if r["type"] == "Comment")
        assert comment_obj["content"] == "My comment"

    def test_review_post_type_data(self):
        review = Review.update_item_review(
            self.book, self.identity, "Test Review", "Review body", visibility=0
        )
        assert review is not None
        post_id = review.latest_post_id
        assert post_id is not None
        post = Takahe.get_post(post_id)
        assert post is not None
        related = post.type_data["object"]["relatedWith"]
        assert len(related) == 1
        assert related[0]["type"] == "Review"
        assert related[0]["name"] == "Test Review"
        assert related[0]["content"] == "Review body"
        assert related[0]["mediaType"] == "text/markdown"

    def test_note_post_type_data_without_progress(self):
        note = Note.objects.create(
            item=self.book,
            owner=self.identity,
            content="Notes without progress",
            visibility=0,
        )
        post_id = note.latest_post_id
        assert post_id is not None
        post = Takahe.get_post(post_id)
        assert post is not None
        related = post.type_data["object"]["relatedWith"]
        assert len(related) == 1
        assert related[0]["type"] == "Note"
        assert related[0]["content"] == "Notes without progress"
        assert "progress" not in related[0]

    def test_note_post_type_data_with_progress(self):
        note = Note.objects.create(
            item=self.book,
            owner=self.identity,
            content="Reading at page 42",
            progress_type=Note.ProgressType.PAGE,
            progress_value="42",
            visibility=0,
        )
        post_id = note.latest_post_id
        assert post_id is not None
        post = Takahe.get_post(post_id)
        assert post is not None
        related = post.type_data["object"]["relatedWith"]
        assert len(related) == 1
        note_obj = related[0]
        assert note_obj["type"] == "Note"
        assert note_obj["progress"]["type"] == Note.ProgressType.PAGE
        assert note_obj["progress"]["value"] == "42"

    def test_collection_post_type_data(self):
        collection = Collection.objects.create(
            owner=self.identity,
            title="My Test Collection",
            brief="Brief description",
            visibility=0,
        )
        post_id = collection.latest_post_id
        assert post_id is not None
        post = Takahe.get_post(post_id)
        assert post is not None
        related = post.type_data["object"]["relatedWith"]
        assert len(related) == 1
        assert related[0]["type"] == "Collection"
        assert related[0]["name"] == "My Test Collection"
        assert related[0]["content"] == "Brief description"


@pytest.mark.django_db(databases="__all__")
class TestUpdateByApObject:
    """Verify update_by_ap_object correctly parses ap_objects (round-trip)."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book = Edition.objects.create(title="Test Book")
        self.user = User.register(email="updbap@test.com", username="updbap_user")
        self.identity = self.user.identity

    def test_shelf_member_round_trip(self):
        Mark(self.identity, self.book).update(ShelfType.COMPLETE, visibility=0)
        member = ShelfMember.objects.get(owner=self.identity, item=self.book)
        ap_obj = member.ap_object
        member.delete()
        assert not ShelfMember.objects.filter(
            owner=self.identity, item=self.book
        ).exists()

        post = _make_remote_post(self.identity.pk)
        result = ShelfMember.update_by_ap_object(self.identity, self.book, ap_obj, post)
        assert result is not None
        assert result.parent.shelf_type == ShelfType.COMPLETE
        assert result.local is False

    def test_review_round_trip(self):
        review = Review.update_item_review(
            self.book, self.identity, "My Review", "Review content", visibility=0
        )
        assert review is not None
        ap_obj = review.ap_object
        review.delete()

        post = _make_remote_post(self.identity.pk)
        result = Review.update_by_ap_object(self.identity, self.book, ap_obj, post)
        assert result is not None
        assert result.title == "My Review"
        assert result.body == "Review content"
        assert result.local is False

    def test_review_round_trip_markdown(self):
        """Verify markdown content is preserved when mediaType is text/markdown."""
        review = Review.update_item_review(
            self.book,
            self.identity,
            "Review",
            "**Bold** and _italic_",
            visibility=0,
        )
        assert review is not None
        ap_obj = review.ap_object
        assert ap_obj["mediaType"] == "text/markdown"
        review.delete()

        post = _make_remote_post(self.identity.pk)
        result = Review.update_by_ap_object(self.identity, self.book, ap_obj, post)
        assert result is not None
        assert result.body == "**Bold** and _italic_"

    def test_rating_round_trip(self):
        Rating.update_item_rating(self.book, self.identity, 8, visibility=0)
        rating = Rating.objects.get(owner=self.identity, item=self.book)
        ap_obj = rating.ap_object
        rating.delete()

        post = _make_remote_post(self.identity.pk)
        result = Rating.update_by_ap_object(self.identity, self.book, ap_obj, post)
        assert result is not None
        assert result.grade == 8
        assert result.local is False

    def test_rating_normalized_from_different_scale(self):
        """Ratings on a 1-5 scale are normalized to 1-10."""
        ap_obj = {
            "id": "https://example.com/rating/99",
            "type": "Rating",
            "best": 5,
            "worst": 1,
            "value": 4,  # 4/5 → round(9*(4-1)/(5-1)) + 1 = round(6.75) + 1 = 8
            "published": "2021-01-01T00:00:00+00:00",
            "updated": "2021-01-01T00:00:00+00:00",
            "attributedTo": self.identity.actor_uri,
            "withRegardTo": self.book.absolute_url,
            "href": "https://example.com/rating/99",
        }
        post = _make_remote_post(self.identity.pk)
        result = Rating.update_by_ap_object(self.identity, self.book, ap_obj, post)
        assert result is not None
        assert result.grade == 8

    def test_comment_round_trip(self):
        comment = Comment.comment_item(
            self.book, self.identity, "Original text", visibility=0
        )
        assert comment is not None
        ap_obj = comment.ap_object
        comment.delete()

        post = _make_remote_post(self.identity.pk)
        result = Comment.update_by_ap_object(self.identity, self.book, ap_obj, post)
        assert result is not None
        assert result.text == "Original text"
        assert result.local is False

    def test_comment_round_trip_with_position(self):
        """Timestamp position is preserved through ap_object round-trip."""
        comment = Comment.objects.create(
            item=self.book,
            owner=self.identity,
            text="At this timestamp",
            metadata={"position": "0:30:00"},
            visibility=0,
        )
        ap_obj = comment.ap_object
        assert ap_obj["relatedWithItemPosition"] == "0:30:00"
        comment.delete()

        post = _make_remote_post(self.identity.pk)
        result = Comment.update_by_ap_object(self.identity, self.book, ap_obj, post)
        assert result is not None
        assert result.metadata.get("position") == "0:30:00"

    def test_note_params_from_ap_object_remote(self):
        """Note.params_from_ap_object extracts all fields correctly for a remote post."""
        note = Note.objects.create(
            item=self.book,
            owner=self.identity,
            title="Note Title",
            content="Note content",
            progress_type=Note.ProgressType.PAGE,
            progress_value="100",
            visibility=0,
        )
        ap_obj = note.ap_object
        post = _make_remote_post(self.identity.pk)

        params = Note.params_from_ap_object(post, ap_obj, None)
        assert params["title"] == "Note Title"
        assert params["content"] == "Note content"
        assert params["progress_value"] == "100"
        assert params["progress_type"] == Note.ProgressType.PAGE
        assert params["sensitive"] is False

    def test_note_params_from_ap_object_no_progress(self):
        """Note without progress returns None for progress fields."""
        ap_obj = {
            "id": "https://example.com/note/1",
            "type": "Note",
            "title": None,
            "content": "Simple note",
            "sensitive": False,
            "published": "2021-01-01T00:00:00+00:00",
            "updated": "2021-01-01T00:00:00+00:00",
            "attributedTo": self.identity.actor_uri,
            "withRegardTo": self.book.absolute_url,
            "href": "https://example.com/note/1",
        }
        post = _make_remote_post(self.identity.pk)
        params = Note.params_from_ap_object(post, ap_obj, None)
        assert params["content"] == "Simple note"
        assert params["progress_value"] is None
        assert params["progress_type"] is None

    def test_note_round_trip(self):
        """Full round-trip: create Note, export ap_object, re-import via update_by_ap_object."""
        note = Note.objects.create(
            item=self.book,
            owner=self.identity,
            title="Round trip note",
            content="Content for round trip",
            progress_type=Note.ProgressType.CHAPTER,
            progress_value="3",
            visibility=0,
        )
        ap_obj = note.ap_object
        note.delete()
        assert not Note.objects.filter(owner=self.identity, item=self.book).exists()

        post = _make_remote_post(self.identity.pk, post_id=77777)
        result = Note.update_by_ap_object(self.identity, self.book, ap_obj, post)
        assert result is not None
        assert result.title == "Round trip note"
        assert result.content == "Content for round trip"
        assert result.progress_type == Note.ProgressType.CHAPTER
        assert result.progress_value == "3"
        assert result.local is False
