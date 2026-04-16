import pytest

from catalog.models import Edition
from journal.models import Comment, Mark, ShelfType
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestCommentItem:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="comment@test.com", username="commentuser")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Comment Book")

    def test_create_comment(self):
        comment = Comment.comment_item(self.book, self.identity, "Great book!", 0)
        assert comment is not None
        assert comment.text == "Great book!"
        assert comment.visibility == 0

    def test_update_comment_text(self):
        Comment.comment_item(self.book, self.identity, "First", 0)
        comment = Comment.comment_item(self.book, self.identity, "Updated", 0)
        assert comment is not None
        assert comment.text == "Updated"
        assert Comment.objects.filter(owner=self.identity, item=self.book).count() == 1

    def test_update_comment_visibility(self):
        Comment.comment_item(self.book, self.identity, "Same text", 0)
        comment = Comment.comment_item(self.book, self.identity, "Same text", 2)
        assert comment is not None
        assert comment.visibility == 2

    def test_no_change_when_same(self):
        c1 = Comment.comment_item(self.book, self.identity, "Same", 0)
        c2 = Comment.comment_item(self.book, self.identity, "Same", 0)
        assert c1 is not None
        assert c2 is not None
        assert c1.pk == c2.pk

    def test_delete_comment_with_none_text(self):
        Comment.comment_item(self.book, self.identity, "To delete", 0)
        assert Comment.objects.filter(owner=self.identity, item=self.book).exists()
        result = Comment.comment_item(self.book, self.identity, None, 0)
        assert result is None
        assert not Comment.objects.filter(owner=self.identity, item=self.book).exists()

    def test_delete_comment_with_empty_text(self):
        Comment.comment_item(self.book, self.identity, "To delete", 0)
        result = Comment.comment_item(self.book, self.identity, "", 0)
        assert result is None
        assert not Comment.objects.filter(owner=self.identity, item=self.book).exists()

    def test_none_text_when_no_comment(self):
        result = Comment.comment_item(self.book, self.identity, None, 0)
        assert result is None


@pytest.mark.django_db(databases="__all__")
class TestCommentProperties:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="cprop@test.com", username="cpropuser")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Prop Book")

    def test_comment_html(self):
        comment = Comment.objects.create(
            owner=self.identity,
            item=self.book,
            text="Hello **world**",
            visibility=0,
        )
        html = comment.html
        assert isinstance(html, str)
        assert len(html) > 0

    def test_comment_mark_property(self):
        Mark(self.identity, self.book).update(ShelfType.WISHLIST)
        comment = Comment.objects.create(
            owner=self.identity,
            item=self.book,
            text="Nice",
            visibility=0,
        )
        mark = comment.mark
        assert mark.owner == self.identity
        assert mark.item == self.book
        assert mark.comment == comment

    def test_item_url_without_position(self):
        comment = Comment.objects.create(
            owner=self.identity,
            item=self.book,
            text="Test",
            visibility=0,
        )
        assert comment.item_url == self.book.url

    def test_item_url_without_position_explicit(self):
        comment = Comment.objects.create(
            owner=self.identity,
            item=self.book,
            text="Test",
            visibility=0,
            metadata={},
        )
        assert comment.item_url == self.book.url
