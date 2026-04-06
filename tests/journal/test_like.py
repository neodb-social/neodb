import pytest

from catalog.models import Edition
from journal.models import Mark, ShelfType
from journal.models.like import Like
from journal.models.review import Review
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestLike:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user1 = User.register(email="like_user1@test.com", username="like_user1")
        self.user2 = User.register(email="like_user2@test.com", username="like_user2")
        self.book = Edition.objects.create(title="Like Test Book")
        # Create a review to like
        mark = Mark(self.user1.identity, self.book)
        mark.update(ShelfType.COMPLETE, "Liked this", visibility=0, rating_grade=8)
        self.review = Review.objects.create(
            owner=self.user1.identity,
            item=self.book,
            title="Great book",
            body="Really enjoyed reading this.",
        )

    def test_user_like_piece_creates_like(self):
        like = Like.user_like_piece(self.user2.identity, self.review)
        assert like is not None
        assert like.owner == self.user2.identity
        assert like.target == self.review

    def test_user_like_piece_idempotent(self):
        like1 = Like.user_like_piece(self.user2.identity, self.review)
        like2 = Like.user_like_piece(self.user2.identity, self.review)
        assert like1.pk == like2.pk

    def test_user_like_piece_none(self):
        result = Like.user_like_piece(self.user1.identity, None)
        assert result is None

    def test_user_liked_piece_true(self):
        Like.user_like_piece(self.user2.identity, self.review)
        assert Like.user_liked_piece(self.user2.identity, self.review) is True

    def test_user_liked_piece_false(self):
        assert Like.user_liked_piece(self.user2.identity, self.review) is False

    def test_user_unlike_piece(self):
        Like.user_like_piece(self.user2.identity, self.review)
        assert Like.user_liked_piece(self.user2.identity, self.review) is True
        Like.user_unlike_piece(self.user2.identity, self.review)
        assert Like.user_liked_piece(self.user2.identity, self.review) is False

    def test_user_unlike_piece_no_existing_like(self):
        # Should not raise
        Like.user_unlike_piece(self.user2.identity, self.review)

    def test_user_unlike_piece_none(self):
        # Should not raise
        Like.user_unlike_piece(self.user1.identity, None)

    def test_user_likes_by_class(self):
        Like.user_like_piece(self.user2.identity, self.review)
        likes = Like.user_likes_by_class(self.user2.identity, Review)
        assert likes.count() == 1
        assert likes.first().target == self.review

    def test_user_likes_by_class_empty(self):
        likes = Like.user_likes_by_class(self.user2.identity, Review)
        assert likes.count() == 0

    def test_multiple_users_can_like_same_piece(self):
        Like.user_like_piece(self.user1.identity, self.review)
        Like.user_like_piece(self.user2.identity, self.review)
        assert Like.user_liked_piece(self.user1.identity, self.review) is True
        assert Like.user_liked_piece(self.user2.identity, self.review) is True
