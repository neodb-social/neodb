import pytest
from pytest_httpx import HTTPXMock

from activities.models import Post, PostInteraction, PostInteractionStates
from users.models import InboxMessage
from users.models.inbox_message import InboxMessageStates

GROUP_URI = "https://lemmy.test/c/books"
AUTHOR_URI = "https://lemmy.test/u/alice"
PAGE_URI = "https://lemmy.test/post/1"
COMMENT_URI = "https://lemmy.test/comment/1"


def _mock_author(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=AUTHOR_URI,
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": ["https://www.w3.org/ns/activitystreams"],
            "id": AUTHOR_URI,
            "type": "Person",
            "preferredUsername": "alice",
        },
    )


def _page_json(content="<p>The real body from origin</p>", name="Book thread"):
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": PAGE_URI,
        "type": "Page",
        "attributedTo": AUTHOR_URI,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "audience": GROUP_URI,
        "name": name,
        "content": content,
        "published": "2026-07-01T10:00:00Z",
    }


def _comment_json():
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": COMMENT_URI,
        "type": "Note",
        "attributedTo": AUTHOR_URI,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "inReplyTo": PAGE_URI,
        "content": "<p>A comment</p>",
        "published": "2026-07-01T11:00:00Z",
    }


def _announce(inner, announce_id="https://lemmy.test/activities/announce/1"):
    return {
        "id": announce_id,
        "type": "Announce",
        "actor": GROUP_URI,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "object": inner,
    }


def _create(obj):
    return {
        "id": "https://lemmy.test/activities/create/1",
        "type": "Create",
        "actor": AUTHOR_URI,
        "object": obj,
    }


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_announced_create_ingests_from_origin_and_boosts(
    httpx_mock: HTTPXMock, config_system
):
    """
    Announce(Create(Page)) from a group actor must ingest the page from its
    origin server (never the embedded copy) and boost it as the group.
    """
    _mock_author(httpx_mock)
    httpx_mock.add_response(
        url=PAGE_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_page_json(),
    )
    embedded = _page_json(content="<p>EMBEDDED FORGERY</p>")
    Post.handle_announced_activity_ap(_announce(_create(embedded)))

    post = Post.objects.get(object_uri=PAGE_URI)
    assert "The real body from origin" in post.content
    assert "EMBEDDED FORGERY" not in post.content
    # Page title is preserved in the converted status content
    assert "Book thread" in post.content
    boost = PostInteraction.objects.get(post=post, type=PostInteraction.Types.boost)
    assert boost.identity.actor_uri == GROUP_URI


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_double_announce_creates_single_boost(httpx_mock: HTTPXMock, config_system):
    """
    Lemmy announces each post twice (Announce(Create(Page)) plus a bare
    Announce(Page) for Mastodon compatibility); only one boost may result.
    """
    _mock_author(httpx_mock)
    httpx_mock.add_response(
        url=PAGE_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_page_json(),
    )
    Post.handle_announced_activity_ap(
        _announce(_create(_page_json()), "https://lemmy.test/activities/announce/1")
    )
    # The compat bare announce takes the regular boost path
    PostInteraction.handle_ap(
        _announce(PAGE_URI, "https://lemmy.test/activities/announce/2")
    )

    post = Post.objects.get(object_uri=PAGE_URI)
    assert (
        PostInteraction.objects.filter(
            post=post,
            type=PostInteraction.Types.boost,
            state__in=PostInteractionStates.group_active(),
        ).count()
        == 1
    )


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_announced_comment_ingests_without_boost(httpx_mock: HTTPXMock, config_system):
    """
    Announced replies (Lemmy comments) are ingested so they thread under
    their parent, but must not be boosted onto follower timelines.
    """
    _mock_author(httpx_mock)
    httpx_mock.add_response(
        url=PAGE_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_page_json(),
    )
    httpx_mock.add_response(
        url=COMMENT_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_comment_json(),
    )
    Post.handle_announced_activity_ap(
        _announce(_create(_page_json()), "https://lemmy.test/activities/announce/1")
    )
    Post.handle_announced_activity_ap(
        _announce(_create(_comment_json()), "https://lemmy.test/activities/announce/2")
    )

    comment = Post.objects.get(object_uri=COMMENT_URI)
    assert comment.in_reply_to == PAGE_URI
    assert not PostInteraction.objects.filter(
        post=comment, type=PostInteraction.Types.boost
    ).exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_announced_update_refreshes_from_origin(httpx_mock: HTTPXMock, config_system):
    _mock_author(httpx_mock)
    httpx_mock.add_response(
        url=PAGE_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_page_json(),
    )
    Post.handle_announced_activity_ap(_announce(_create(_page_json())))
    assert "The real body from origin" in Post.objects.get(object_uri=PAGE_URI).content

    httpx_mock.add_response(
        url=PAGE_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_page_json(content="<p>Edited on origin</p>"),
    )
    Post.handle_announced_activity_ap(
        _announce(
            {
                "id": "https://lemmy.test/activities/update/1",
                "type": "Update",
                "actor": AUTHOR_URI,
                "object": _page_json(content="<p>EMBEDDED EDIT FORGERY</p>"),
            },
            "https://lemmy.test/activities/announce/3",
        )
    )
    post = Post.objects.get(object_uri=PAGE_URI)
    assert "Edited on origin" in post.content
    assert "FORGERY" not in post.content


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_announced_delete_verified_against_origin(httpx_mock: HTTPXMock, config_system):
    _mock_author(httpx_mock)
    httpx_mock.add_response(
        url=PAGE_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_page_json(),
    )
    Post.handle_announced_activity_ap(_announce(_create(_page_json())))
    assert Post.objects.filter(object_uri=PAGE_URI).exists()

    httpx_mock.add_response(url=PAGE_URI, status_code=410)
    Post.handle_announced_activity_ap(
        _announce(
            {
                "id": "https://lemmy.test/activities/delete/1",
                "type": "Delete",
                "actor": AUTHOR_URI,
                "object": PAGE_URI,
            },
            "https://lemmy.test/activities/announce/4",
        )
    )
    assert not Post.objects.filter(object_uri=PAGE_URI).exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_announced_update_of_unknown_post_is_ignored(
    httpx_mock: HTTPXMock, config_system
):
    Post.handle_announced_activity_ap(
        _announce(
            {
                "id": "https://lemmy.test/activities/update/9",
                "type": "Update",
                "actor": AUTHOR_URI,
                "object": _page_json(),
            }
        )
    )
    assert not Post.objects.filter(object_uri=PAGE_URI).exists()
    assert not any(str(r.url) == PAGE_URI for r in httpx_mock.get_requests())


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_announced_multi_typed_activity_processed(httpx_mock: HTTPXMock, config_system):
    """
    JSON-LD allows the inner activity to carry multiple types, e.g.
    ["Activity", "Create"]; the concrete type must be used.
    """
    _mock_author(httpx_mock)
    httpx_mock.add_response(
        url=PAGE_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_page_json(),
    )
    create = _create(_page_json())
    create["type"] = ["Activity", "Create"]
    Post.handle_announced_activity_ap(_announce(create))
    assert Post.objects.filter(object_uri=PAGE_URI).exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_inbox_dispatch_routes_announced_activities(
    httpx_mock: HTTPXMock, config_system
):
    """
    InboxMessage processing routes Announce(Create) to the announced
    activity handler and still drops announced votes.
    """
    _mock_author(httpx_mock)
    httpx_mock.add_response(
        url=PAGE_URI,
        headers={"Content-Type": "application/activity+json"},
        json=_page_json(),
    )
    message = InboxMessage.objects.create(message=_announce(_create(_page_json())))
    assert InboxMessageStates.handle_received(message) == InboxMessageStates.processed
    assert Post.objects.filter(object_uri=PAGE_URI).exists()

    vote = InboxMessage.objects.create(
        message=_announce(
            {
                "id": "https://lemmy.test/activities/like/1",
                "type": "Like",
                "actor": AUTHOR_URI,
                "object": PAGE_URI,
            },
            "https://lemmy.test/activities/announce/5",
        )
    )
    assert InboxMessageStates.handle_received(vote) == InboxMessageStates.processed
    assert not PostInteraction.objects.filter(type=PostInteraction.Types.like).exists()
