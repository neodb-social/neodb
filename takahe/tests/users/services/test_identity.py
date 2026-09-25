import pytest

from activities.models import Post, PostInteraction, PostInteractionStates
from users.models import Identity
from users.services import IdentityService


def _pins(identity: Identity) -> dict[str, str]:
    return {
        i.post.object_uri: i.state
        for i in PostInteraction.objects.filter(
            type=PostInteraction.Types.pin, identity=identity
        ).select_related("post")
    }


@pytest.mark.django_db
def test_sync_pins(
    remote_identity: Identity, django_assert_num_queries, django_assert_max_num_queries
):
    uris = [f"https://remote.test/posts/{n}/" for n in range(5)]
    posts = [
        Post.objects.create(author=remote_identity, local=False, object_uri=uri)
        for uri in uris
    ]
    for post in posts[:3]:
        PostInteraction.objects.create(
            type=PostInteraction.Types.pin, identity=remote_identity, post=post
        )
    remote_identity = Identity.objects.select_related("domain").get(
        pk=remote_identity.pk
    )
    service = IdentityService(remote_identity)

    with django_assert_max_num_queries(8):
        service.sync_pins([uris[1], uris[2], uris[3], uris[1]])

    active = PostInteractionStates.group_active()
    pins = _pins(remote_identity)
    assert pins[uris[0]] == PostInteractionStates.undone_fanned_out
    assert all(pins[uri] in active for uri in uris[1:4])
    assert uris[4] not in pins
    assert PostInteraction.objects.filter(post=posts[1]).count() == 1

    # savepoint, posts, pins, release
    with django_assert_num_queries(4):
        service.sync_pins(uris[1:4])
