import pytest
from pytest_httpx import HTTPXMock

from activities.models import Post, PostInteraction, PostTombstone
from core.exceptions import ActivityPubFormatError, ActorMismatchError
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


def _note_response(
    httpx_mock: HTTPXMock, url: str, note_id: str, author: Identity
) -> None:
    httpx_mock.add_response(
        url=url,
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": ["https://www.w3.org/ns/activitystreams"],
            "id": note_id,
            "type": "Note",
            "attributedTo": author.actor_uri,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "content": f"served at {url}",
        },
    )


@pytest.mark.django_db
def test_fetch_refuses_same_host_document_claiming_another_post(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    """
    Another path on the post's own host cannot speak for the post: the
    stored copy is returned untouched.
    """
    post = _remote_post(remote_identity)
    other = "https://remote.test/uploads/x.json"
    _note_response(httpx_mock, other, VICTIM_POST_URI, remote_identity)
    assert Post.by_object_uri(other, fetch=True) == post
    assert Post.objects.get(pk=post.pk).content == "original"
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.django_db
def test_fetch_follows_canonical_id(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    page = "https://remote.test/@test/5"
    canonical = "https://remote.test/posts/5/"
    _note_response(httpx_mock, page, canonical, remote_identity)
    _note_response(httpx_mock, canonical, canonical, remote_identity)
    post = Post.by_object_uri(page, fetch=True)
    assert post.object_uri == canonical
    assert post.content == f"served at {canonical}"


@pytest.mark.django_db
def test_fetch_refuses_canonical_document_with_another_id(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    page = "https://remote.test/@test/5"
    canonical = "https://remote.test/posts/5/"
    _note_response(httpx_mock, page, canonical, remote_identity)
    _note_response(
        httpx_mock, canonical, "https://remote.test/posts/6/", remote_identity
    )
    with pytest.raises(Post.DoesNotExist):
        Post.by_object_uri(page, fetch=True)
    assert not Post.objects.filter(
        object_uri__startswith="https://remote.test/posts/"
    ).exists()


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
    # The author is still found inside a PeerTube-style list
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


def _neighbour(remote_identity: Identity, actor_type: str = "person") -> Identity:
    return Identity.objects.create(
        actor_uri="https://remote.test/neighbour/",
        profile_uri="https://remote.test/@neighbour/",
        username="neighbour",
        domain=remote_identity.domain,
        local=False,
        state="updated",
        actor_type=actor_type,
    )


@pytest.mark.django_db
def test_create_refuses_object_on_other_host_than_signer(
    remote_identity: Identity, remote_identity2: Identity
):
    """
    The signer may be any attributedTo entry, but the post is stored under
    the signer, so its id must be on the signer's host.
    """
    forged_uri = "https://remote.test/posts/forged/"
    with pytest.raises(ActivityPubFormatError):
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
def test_update_refuses_coauthor_on_other_host(
    remote_identity: Identity, remote_identity2: Identity
):
    post = _remote_post(remote_identity)
    with pytest.raises(ActivityPubFormatError):
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
def test_create_stores_post_under_signing_coauthor(remote_identity: Identity):
    """
    A neighbour listed first does not become the author of a post another
    same-host actor signed.
    """
    attacker = _neighbour(remote_identity)
    uri = "https://remote.test/posts/forged/"
    Post.handle_create_ap(
        {
            "type": "Create",
            "actor": attacker.actor_uri,
            "object": {
                "id": uri,
                "type": "Note",
                "attributedTo": [remote_identity.actor_uri, attacker.actor_uri],
                "to": ["as:Public"],
                "content": "FORGED",
            },
        }
    )
    assert Post.objects.get(object_uri=uri).author == attacker


@pytest.mark.django_db
def test_update_by_same_host_coauthor_takes_origin_content(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    """
    A neighbour listing the author first cannot supply the content: the
    copy is refreshed from the origin instead.
    """
    post = _remote_post(remote_identity)
    attacker = _neighbour(remote_identity)
    _note_response(httpx_mock, VICTIM_POST_URI, VICTIM_POST_URI, remote_identity)
    Post.handle_update_ap(
        {
            "type": "Update",
            "actor": attacker.actor_uri,
            "object": {
                "id": VICTIM_POST_URI,
                "type": "Note",
                "attributedTo": [remote_identity.actor_uri, attacker.actor_uri],
                "to": ["as:Public"],
                "content": "OVERWRITTEN",
                "updated": "2026-07-02T10:00:00Z",
            },
        }
    )
    post = Post.objects.get(pk=post.pk)
    assert post.content == f"served at {VICTIM_POST_URI}"
    assert post.author == remote_identity


@pytest.mark.django_db
def test_update_by_same_host_coauthor_keeps_non_public_copy(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    """
    The origin answers 404 to the system actor for a followers-only post,
    which must not delete our copy.
    """
    post = _remote_post(remote_identity)
    Post.objects.filter(pk=post.pk).update(visibility=Post.Visibilities.followers)
    attacker = _neighbour(remote_identity)
    httpx_mock.add_response(url=VICTIM_POST_URI, status_code=404)
    Post.handle_update_ap(
        {
            "type": "Update",
            "actor": attacker.actor_uri,
            "object": {
                "id": VICTIM_POST_URI,
                "type": "Note",
                "attributedTo": [remote_identity.actor_uri, attacker.actor_uri],
                "to": ["as:Public"],
                "content": "OVERWRITTEN",
                "updated": "2026-07-02T10:00:00Z",
            },
        }
    )
    assert Post.objects.get(pk=post.pk).content == "original"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "visibility,deleted",
    [
        (Post.Visibilities.public, True),
        (Post.Visibilities.unlisted, True),
        (Post.Visibilities.followers, False),
        (Post.Visibilities.mentioned, False),
    ],
)
def test_origin_refresh_404_deletes_only_public_copies(
    httpx_mock: HTTPXMock,
    remote_identity: Identity,
    config_system,
    visibility: int,
    deleted: bool,
):
    post = _remote_post(remote_identity)
    Post.objects.filter(pk=post.pk).update(visibility=visibility)
    httpx_mock.add_response(url=VICTIM_POST_URI, status_code=404)
    Post.refresh_from_origin(VICTIM_POST_URI)
    assert Post.objects.filter(pk=post.pk).exists() is not deleted


@pytest.mark.django_db
def test_update_by_actor_not_listing_author_is_refused(
    httpx_mock: HTTPXMock, remote_identity: Identity
):
    post = _remote_post(remote_identity)
    attacker = _neighbour(remote_identity)
    with pytest.raises(ActorMismatchError):
        Post.handle_update_ap(
            {
                "type": "Update",
                "actor": attacker.actor_uri,
                "object": {
                    "id": VICTIM_POST_URI,
                    "type": "Note",
                    "attributedTo": attacker.actor_uri,
                    "to": ["as:Public"],
                    "content": "OVERWRITTEN",
                    "updated": "2026-07-02T10:00:00Z",
                },
            }
        )
    assert Post.objects.get(pk=post.pk).content == "original"
    assert not httpx_mock.get_requests()


@pytest.mark.django_db
def test_update_by_group_refreshes_copy_stored_under_person(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    group = _neighbour(remote_identity, actor_type="group")
    uri = "https://remote.test/posts/blog-3/"
    attributed = [remote_identity.actor_uri, group.actor_uri]
    Post.by_ap(
        {
            "id": uri,
            "type": "Note",
            "attributedTo": list(attributed),
            "to": ["as:Public"],
            "content": "Hello",
            "published": "2026-07-01T10:00:00Z",
        },
        create=True,
    )
    assert Post.objects.get(object_uri=uri).author == remote_identity
    _note_response(httpx_mock, uri, uri, remote_identity)
    Post.handle_update_ap(
        {
            "type": "Update",
            "actor": group.actor_uri,
            "object": {
                "id": uri,
                "type": "Note",
                "attributedTo": list(attributed),
                "to": ["as:Public"],
                "content": "from the update",
                "updated": "2026-07-02T10:00:00Z",
            },
        }
    )
    post = Post.objects.get(object_uri=uri)
    assert post.content == f"served at {uri}"
    assert post.author == remote_identity


@pytest.mark.django_db
def test_update_accepts_author_listed_with_channel(remote_identity: Identity):
    post = _remote_post(remote_identity)
    channel = _neighbour(remote_identity, actor_type="group")
    Post.handle_update_ap(
        {
            "type": "Update",
            "actor": remote_identity.actor_uri,
            "object": {
                "id": VICTIM_POST_URI,
                "type": "Note",
                "attributedTo": [channel.actor_uri, remote_identity.actor_uri],
                "to": ["as:Public"],
                "content": "edited",
                "updated": "2026-07-02T10:00:00Z",
            },
        }
    )
    assert Post.objects.get(pk=post.pk).content == "edited"


@pytest.mark.django_db
def test_create_by_group_coauthor_stores_post_under_group(remote_identity: Identity):
    group = _neighbour(remote_identity, actor_type="group")
    uri = "https://remote.test/posts/blog-1/"
    Post.handle_create_ap(
        {
            "type": "Create",
            "actor": group.actor_uri,
            "object": {
                "id": uri,
                "type": "Article",
                "attributedTo": [remote_identity.actor_uri, group.actor_uri],
                "to": ["as:Public"],
                "content": "Hello",
            },
        }
    )
    assert Post.objects.get(object_uri=uri).author == group


@pytest.mark.django_db
def test_origin_refresh_updates_group_signed_post(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    """
    The origin lists the person first, but the copy belongs to the group
    that signed its Create; a refresh still applies.
    """
    group = _neighbour(remote_identity, actor_type="group")
    uri = "https://remote.test/posts/blog-1/"
    note = {
        "id": uri,
        "type": "Note",
        "attributedTo": [remote_identity.actor_uri, group.actor_uri],
        "to": ["as:Public"],
        "content": "Hello",
        "published": "2026-07-01T10:00:00Z",
    }
    Post.handle_create_ap(
        {"type": "Create", "actor": group.actor_uri, "object": dict(note)}
    )
    httpx_mock.add_response(
        url=uri,
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": ["https://www.w3.org/ns/activitystreams"],
            **note,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "content": "edited",
            "updated": "2026-07-02T10:00:00Z",
        },
    )
    post = Post.refresh_from_origin(uri)
    assert post is not None
    post = Post.objects.get(pk=post.pk)
    assert post.content == "edited"
    assert post.author == group


@pytest.mark.django_db
def test_origin_refresh_ignores_coauthor_on_other_host(
    httpx_mock: HTTPXMock,
    remote_identity: Identity,
    remote_identity2: Identity,
    config_system,
):
    group = _neighbour(remote_identity, actor_type="group")
    uri = "https://remote.test/posts/blog-2/"
    note = {
        "id": uri,
        "type": "Note",
        "attributedTo": [remote_identity2.actor_uri, group.actor_uri],
        "to": ["as:Public"],
        "content": "Hello",
        "published": "2026-07-01T10:00:00Z",
    }
    Post.handle_create_ap(
        {"type": "Create", "actor": group.actor_uri, "object": dict(note)}
    )
    httpx_mock.add_response(
        url=uri,
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": ["https://www.w3.org/ns/activitystreams"],
            **note,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "content": "edited",
            "updated": "2026-07-02T10:00:00Z",
        },
    )
    Post.refresh_from_origin(uri)
    post = Post.objects.get(object_uri=uri)
    assert post.content == "edited"
    assert post.author == group


def _create(author: Identity, uri: str) -> dict:
    return {
        "type": "Create",
        "actor": author.actor_uri,
        "object": {
            "id": uri,
            "type": "Note",
            "attributedTo": author.actor_uri,
            "to": ["as:Public"],
            "content": "Hello",
        },
    }


@pytest.mark.django_db
def test_replayed_create_after_delete_is_ignored(
    remote_identity: Identity, config_system
):
    uri = "https://remote.test/posts/deleted/"
    Post.handle_create_ap(_create(remote_identity, uri))
    Post.handle_delete_ap(
        {"type": "Delete", "actor": remote_identity.actor_uri, "object": uri}
    )
    assert not Post.objects.filter(object_uri=uri).exists()
    Post.handle_create_ap(_create(remote_identity, uri))
    assert not Post.objects.filter(object_uri=uri).exists()


@pytest.mark.django_db
def test_refused_delete_leaves_no_tombstone(remote_identity: Identity):
    _remote_post(remote_identity)
    attacker = _neighbour(remote_identity)
    with pytest.raises(ActorMismatchError):
        Post.handle_delete_ap(
            {"type": "Delete", "actor": attacker.actor_uri, "object": VICTIM_POST_URI}
        )
    assert not PostTombstone.objects.exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_poll_refresh_refuses_document_with_another_id(
    httpx_mock: HTTPXMock, remote_identity: Identity, config_system
):
    victim = _remote_post(remote_identity)
    poll = Post.objects.create(
        author=remote_identity,
        local=False,
        content="<p>Poll</p>",
        object_uri="https://remote.test/status/poll",
        type=Post.Types.question,
        type_data={
            "type": "Question",
            "mode": "oneOf",
            "options": [{"name": "Yes", "type": "Note", "votes": 1}],
            "voter_count": 1,
            "end_time": "2099-01-01T00:00:00Z",
        },
    )
    poll.refresh_from_db()
    _note_response(httpx_mock, poll.object_uri, VICTIM_POST_URI, remote_identity)
    assert poll.refresh_question_from_remote() is None
    assert Post.objects.get(pk=victim.pk).content == "original"


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
