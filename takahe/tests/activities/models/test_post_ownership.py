import pytest
from pytest_httpx import HTTPXMock

from activities.models import Post, PostInteraction
from core.exceptions import ActorMismatchError
from users.models import Identity

VICTIM_POST_URI = "https://remote.test/posts/1/"


def _remote_post(author: Identity, content: str = "original") -> Post:
    post = Post.by_ap(
        {
            "id": VICTIM_POST_URI,
            "type": "Note",
            "attributedTo": author.actor_uri,
            "to": ["as:Public"],
            "content": content,
            "published": "2026-07-01T10:00:00Z",
        },
        create=True,
    )
    return Post.objects.get(pk=post.pk)


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_fetch_refuses_document_with_id_on_another_host(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    """
    A fetched document must not claim the id of a post on another host,
    even when it names that post's real author.
    """
    post = _remote_post(remote_identity)
    httpx_mock.add_response(
        url="https://evil.test/x",
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": ["https://www.w3.org/ns/activitystreams"],
            "id": VICTIM_POST_URI,
            "type": "Note",
            "attributedTo": remote_identity.actor_uri,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "content": "OVERWRITTEN",
        },
    )
    with pytest.raises(Post.DoesNotExist):
        Post.by_object_uri("https://evil.test/x", fetch=True)
    assert Post.objects.get(pk=post.pk).content == "original"


@pytest.mark.django_db
def test_by_ap_never_updates_local_post(identity: Identity, config_system):
    post = Post.create_local(
        author=identity, content="mine", visibility=Post.Visibilities.public
    )
    post = Post.objects.get(pk=post.pk)
    with pytest.raises(ActorMismatchError):
        Post.by_ap(
            {
                "id": post.object_uri,
                "type": "Note",
                "attributedTo": identity.actor_uri,
                "to": ["as:Public"],
                "content": "OVERWRITTEN",
            },
            create=True,
            update=True,
        )
    assert Post.objects.get(pk=post.pk).content == post.content


@pytest.mark.django_db
def test_by_ap_update_requires_stored_author(remote_identity: Identity):
    post = _remote_post(remote_identity)
    with pytest.raises(ActorMismatchError):
        Post.by_ap(
            {
                "id": VICTIM_POST_URI,
                "type": "Note",
                "attributedTo": "https://remote.test/other-actor/",
                "to": ["as:Public"],
                "content": "OVERWRITTEN",
            },
            update=True,
        )
    assert Post.objects.get(pk=post.pk).content == "original"
    # The author is still found inside a WriteFreely-style list
    Post.by_ap(
        {
            "id": VICTIM_POST_URI,
            "type": "Note",
            "attributedTo": [
                "https://remote.test/blog-group/",
                remote_identity.actor_uri,
            ],
            "to": ["as:Public"],
            "content": "edited",
        },
        update=True,
    )
    assert Post.objects.get(pk=post.pk).content == "edited"


@pytest.mark.django_db
def test_create_refuses_actor_on_other_host_than_primary_author(
    remote_identity: Identity, remote_identity2: Identity
):
    """
    The signer may be any attributedTo entry, but the entry by_ap stores
    as author must be on the signer's host.
    """
    forged_uri = "https://remote.test/posts/forged/"
    with pytest.raises(ActorMismatchError):
        Post.handle_create_ap(
            {
                "type": "Create",
                "actor": remote_identity2.actor_uri,
                "object": {
                    "id": forged_uri,
                    "type": "Note",
                    "attributedTo": [
                        remote_identity.actor_uri,
                        remote_identity2.actor_uri,
                    ],
                    "to": ["as:Public"],
                    "content": "FORGED",
                },
            }
        )
    assert not Post.objects.filter(object_uri=forged_uri).exists()


@pytest.mark.django_db
def test_update_refuses_actor_on_other_host_than_primary_author(
    remote_identity: Identity, remote_identity2: Identity
):
    post = _remote_post(remote_identity)
    with pytest.raises(ActorMismatchError):
        Post.handle_update_ap(
            {
                "type": "Update",
                "actor": remote_identity2.actor_uri,
                "object": {
                    "id": VICTIM_POST_URI,
                    "type": "Note",
                    "attributedTo": [
                        remote_identity.actor_uri,
                        remote_identity2.actor_uri,
                    ],
                    "to": ["as:Public"],
                    "content": "OVERWRITTEN",
                },
            }
        )
    assert Post.objects.get(pk=post.pk).content == "original"


@pytest.mark.django_db
def test_create_accepts_writefreely_blog_actor(remote_identity: Identity):
    uri = "https://remote.test/posts/blog-1/"
    Post.handle_create_ap(
        {
            "type": "Create",
            "actor": "https://remote.test/blog-group/",
            "object": {
                "id": uri,
                "type": "Article",
                "attributedTo": [
                    remote_identity.actor_uri,
                    "https://remote.test/blog-group/",
                ],
                "to": ["as:Public"],
                "content": "Hello",
            },
        }
    )
    assert Post.objects.get(object_uri=uri).author == remote_identity


@pytest.mark.django_db
def test_announced_create_leaves_local_post_untouched(
    identity: Identity, config_system
):
    post = Post.create_local(
        author=identity, content="mine", visibility=Post.Visibilities.public
    )
    post = Post.objects.get(pk=post.pk)
    Post.handle_announced_activity_ap(
        {
            "id": "https://lemmy.test/activities/announce/1",
            "type": "Announce",
            "actor": "https://lemmy.test/c/books",
            "object": {
                "type": "Create",
                "actor": identity.actor_uri,
                "object": {
                    "id": post.object_uri,
                    "type": "Note",
                    "attributedTo": identity.actor_uri,
                    "content": "OVERWRITTEN",
                },
            },
        }
    )
    assert Post.objects.get(pk=post.pk).content == post.content
    assert not PostInteraction.objects.filter(post=post).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "visibility",
    [
        Post.Visibilities.followers,
        Post.Visibilities.mentioned,
        Post.Visibilities.local_only,
    ],
)
def test_remote_boost_of_non_public_post_refused(
    identity: Identity, remote_identity: Identity, config_system, visibility: int
):
    post = Post.create_local(
        author=identity, content="<p>private</p>", visibility=visibility
    )
    with pytest.raises(ActorMismatchError):
        PostInteraction.handle_ap(
            {
                "id": "https://remote.test/activities/announce/1",
                "type": "Announce",
                "actor": remote_identity.actor_uri,
                "object": post.object_uri,
            }
        )
    assert not PostInteraction.objects.filter(post=post).exists()


@pytest.mark.django_db
def test_remote_boost_of_public_post_accepted(
    identity: Identity, remote_identity: Identity, config_system
):
    post = Post.create_local(
        author=identity, content="<p>public</p>", visibility=Post.Visibilities.public
    )
    PostInteraction.handle_ap(
        {
            "id": "https://remote.test/activities/announce/1",
            "type": "Announce",
            "actor": remote_identity.actor_uri,
            "object": post.object_uri,
        }
    )
    assert PostInteraction.objects.filter(
        post=post, type=PostInteraction.Types.boost, identity=remote_identity
    ).exists()
