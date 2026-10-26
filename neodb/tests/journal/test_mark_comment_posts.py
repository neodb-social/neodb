import pytest

from catalog.models import Edition
from journal.models import Comment, Mark, ShelfType
from takahe.models import Post
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def test_clearing_the_comment_keeps_the_mark_posts() -> None:
    user = User.register(email="marker@example.com", username="marker")
    book = Edition.objects.create(title="Commented Book")
    mark = Mark(user.identity, book)
    mark.update(ShelfType.WISHLIST, "want it", None, [], 0)
    mark.update(ShelfType.COMPLETE, "loved it", 8, [], 0)
    mark = Mark(user.identity, book)
    comment = Comment.objects.get(owner=user.identity, item=book)
    member = mark.shelfmember
    assert member is not None
    post_ids = set(comment.all_post_ids) | set(member.all_post_ids)
    assert len(post_ids) == 2

    mark.update(ShelfType.COMPLETE, "", 8, [], 0)

    assert not Comment.objects.filter(owner=user.identity, item=book).exists()
    states = set(Post.objects.filter(pk__in=post_ids).values_list("state", flat=True))
    assert "deleted" not in states
    member = Mark(user.identity, book).shelfmember
    assert member is not None
    latest = member.latest_post
    assert latest is not None
    assert latest.pk in post_ids
