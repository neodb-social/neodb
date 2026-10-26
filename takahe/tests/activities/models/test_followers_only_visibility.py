import pytest

from activities.models import Post
from users.models import Follow, FollowStates
from users.models.identity import _plausible_username


@pytest.mark.django_db
def test_pending_follow_does_not_unlock_followers_only_posts(
    identity, other_identity, config_system
):
    secret = Post.create_local(
        author=other_identity, content="secret", visibility=Post.Visibilities.followers
    )
    follow = Follow.create_local(source=identity, target=other_identity)
    follow.state = FollowStates.pending_approval
    follow.save(update_fields=["state"])

    visible = Post.objects.not_hidden().visible_to(identity).filter(pk=secret.pk)
    assert not visible.exists()

    follow.state = FollowStates.accepted
    follow.save(update_fields=["state"])
    assert visible.exists()


@pytest.mark.parametrize(
    "username, ok",
    [
        ("alice", True),
        ("Alice_O.Neil-2", True),
        ("x end init call alert(document.domain) then log", False),
        ('a"b', False),
        ("a/b", False),
        ("a@b", False),
    ],
)
def test_plausible_username(username: str, ok: bool) -> None:
    assert _plausible_username(username) is ok
