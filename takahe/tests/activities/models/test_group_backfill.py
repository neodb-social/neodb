from datetime import timedelta

import pytest
from django.utils import timezone
from pytest_httpx import HTTPXMock

from core.ld import format_ld_date

from activities.models import (
    FanOut,
    Post,
    PostInteraction,
    PostInteractionStates,
    PostStates,
    TimelineEvent,
)
from users.models import Domain, Follow, Identity, InboxMessage
from users.models.follow import FollowStates

GROUP_URI = "https://lemmy.test/c/books"
OUTBOX_URI = "https://lemmy.test/c/books/outbox"
AUTHOR_URI = "https://lemmy.test/u/alice"
PAGE_URI = "https://lemmy.test/post/1"
COMMENT_URI = "https://lemmy.test/comment/1"
ANNOUNCE_ID = "https://lemmy.test/activities/announce/create/1"
AP_HEADERS = {"Content-Type": "application/activity+json"}

any_request = pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False,
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)


@pytest.fixture
def group(db) -> Identity:
    domain = Domain.objects.create(domain="lemmy.test", local=False, state="updated")
    return Identity.objects.create(
        actor_uri=GROUP_URI,
        inbox_uri="https://lemmy.test/c/books/inbox",
        outbox_uri=OUTBOX_URI,
        username="books",
        domain=domain,
        actor_type="group",
        local=False,
        state="updated",
    )


@pytest.fixture
def followed_group(group, identity) -> Identity:
    Follow.objects.create(source=identity, target=group, state="accepted")
    return group


def _page_json() -> dict:
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": PAGE_URI,
        "type": "Page",
        "attributedTo": AUTHOR_URI,
        "to": [GROUP_URI, "https://www.w3.org/ns/activitystreams#Public"],
        "audience": GROUP_URI,
        "name": "Book thread",
        "content": "<p>Body</p>",
        # recent, so its age does not keep it out of a home timeline
        "published": format_ld_date(timezone.now() - timedelta(hours=2)),
    }


def _announce(obj, announce_id: str = ANNOUNCE_ID, actor: str = GROUP_URI) -> dict:
    return {"id": announce_id, "type": "Announce", "actor": actor, "object": obj}


def _create(obj) -> dict:
    return {
        "id": "https://lemmy.test/activities/create/1",
        "type": "Create",
        "actor": AUTHOR_URI,
        "object": obj,
    }


def _mock_origin(httpx_mock: HTTPXMock, page: dict | None = None) -> None:
    httpx_mock.add_response(
        url=AUTHOR_URI,
        headers=AP_HEADERS,
        json={
            "@context": ["https://www.w3.org/ns/activitystreams"],
            "id": AUTHOR_URI,
            "type": "Person",
            "preferredUsername": "alice",
        },
    )
    httpx_mock.add_response(url=PAGE_URI, headers=AP_HEADERS, json=page or _page_json())


def _mock_outbox(httpx_mock: HTTPXMock, items: list) -> None:
    httpx_mock.add_response(
        url=OUTBOX_URI,
        headers=AP_HEADERS,
        json={
            "@context": ["https://www.w3.org/ns/activitystreams"],
            "id": OUTBOX_URI,
            "type": "OrderedCollection",
            "totalItems": len(items),
            "orderedItems": items,
        },
    )


def _queued(message_type: str) -> list[dict]:
    return [
        m.message["object"]
        for m in InboxMessage.objects.filter(message__type="__internal__")
        if m.message["object"].get("type") == message_type
    ]


@pytest.mark.django_db
@any_request
def test_outbox_fetch_queues_only_what_the_group_announced(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    _mock_outbox(
        httpx_mock,
        [
            _announce(_create(_page_json())),
            # the same thread again, as a bare announce
            _announce(PAGE_URI, "https://lemmy.test/activities/announce/2"),
            _announce(
                "https://lemmy.test/post/2", "https://lemmy.test/activities/announce/3"
            ),
            # announced by somebody else
            _announce("https://lemmy.test/post/3", actor="https://lemmy.test/c/other"),
            # an id the group's server could not have minted
            _announce("https://lemmy.test/post/4", "https://evil.test/announce/1"),
            # a relayed vote
            _announce(
                {"type": "Like", "object": "https://lemmy.test/post/5"},
                "https://lemmy.test/activities/announce/5",
            ),
            {"id": "https://lemmy.test/x", "type": "Create", "actor": GROUP_URI},
            "https://lemmy.test/activities/announce/6",
        ],
    )

    Post.handle_fetch_group_outbox({"identity": followed_group.pk})

    assert _queued("FetchAnnounced") == [
        {
            "type": "FetchAnnounced",
            "identity": followed_group.pk,
            "object": PAGE_URI,
            "id": ANNOUNCE_ID,
        },
        {
            "type": "FetchAnnounced",
            "identity": followed_group.pk,
            "object": "https://lemmy.test/post/2",
            "id": "https://lemmy.test/activities/announce/3",
        },
    ]


@pytest.mark.django_db
def test_outbox_fetch_needs_a_local_follower(
    httpx_mock: HTTPXMock, config_system, group, remote_identity
):
    Follow.objects.create(source=remote_identity, target=group, state="accepted")

    Post.handle_fetch_group_outbox({"identity": group.pk})

    assert httpx_mock.get_requests() == []
    assert _queued("FetchAnnounced") == []


@pytest.mark.django_db
def test_outbox_fetch_ignores_an_outbox_on_another_host(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    followed_group.outbox_uri = "https://elsewhere.test/outbox"
    followed_group.save()

    Post.handle_fetch_group_outbox({"identity": followed_group.pk})

    assert httpx_mock.get_requests() == []


@pytest.mark.django_db
@any_request
def test_outbox_fetch_runs_once_per_interval(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    _mock_outbox(httpx_mock, [_announce(PAGE_URI)])

    Post.handle_fetch_group_outbox({"identity": followed_group.pk})
    Post.handle_fetch_group_outbox({"identity": followed_group.pk})

    assert len(httpx_mock.get_requests(url=OUTBOX_URI)) == 1
    assert len(_queued("FetchAnnounced")) == 1


@pytest.mark.django_db
@any_request
def test_outbox_fetch_follows_a_first_page_link(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    page_uri = OUTBOX_URI + "?page=1"
    httpx_mock.add_response(
        url=OUTBOX_URI,
        headers=AP_HEADERS,
        json={"id": OUTBOX_URI, "type": "OrderedCollection", "first": page_uri},
    )
    httpx_mock.add_response(
        url=page_uri,
        headers=AP_HEADERS,
        json={"type": "OrderedCollectionPage", "orderedItems": [_announce(PAGE_URI)]},
    )

    Post.handle_fetch_group_outbox({"identity": followed_group.pk})

    assert [m["object"] for m in _queued("FetchAnnounced")] == [PAGE_URI]


@pytest.mark.django_db
@any_request
def test_outbox_fetch_skips_threads_already_listed(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    _mock_origin(httpx_mock)
    _mock_outbox(httpx_mock, [_announce(_create(_page_json()))])
    Post.handle_fetch_announced(
        {"identity": followed_group.pk, "object": PAGE_URI, "id": ANNOUNCE_ID}
    )

    Post.handle_fetch_group_outbox({"identity": followed_group.pk})

    assert _queued("FetchAnnounced") == []


@pytest.mark.django_db
@any_request
def test_fetch_announced_lists_the_thread_without_fan_out(
    httpx_mock: HTTPXMock, config_system, followed_group, identity, stator
):
    _mock_origin(httpx_mock)

    Post.handle_fetch_announced(
        {"identity": followed_group.pk, "object": PAGE_URI, "id": ANNOUNCE_ID}
    )
    stator.run_single_cycle()

    post = Post.objects.get(object_uri=PAGE_URI)
    boost = PostInteraction.objects.get(post=post, type=PostInteraction.Types.boost)
    assert boost.identity == followed_group
    assert boost.object_uri == ANNOUNCE_ID
    assert boost.state == PostInteractionStates.fanned_out
    assert boost.published == post.published
    assert post.stats["boosts"] == 1
    # listed under the group, absent from its follower's home timeline
    assert TimelineEvent.objects.filter(
        identity=followed_group, subject_post_interaction=boost
    ).exists()
    assert not FanOut.objects.filter(subject_post_interaction=boost).exists()
    assert not TimelineEvent.objects.filter(
        identity=identity, subject_post=post
    ).exists()


@pytest.mark.django_db
@any_request
def test_fetch_announced_does_not_deliver_the_post_it_creates(
    httpx_mock: HTTPXMock, config_system, followed_group, identity, stator
):
    page = _page_json()
    page["tag"] = [
        {"type": "Mention", "href": identity.actor_uri, "name": f"@{identity.handle}"}
    ]
    _mock_origin(httpx_mock, page)

    Post.handle_fetch_announced(
        {"identity": followed_group.pk, "object": PAGE_URI, "id": ANNOUNCE_ID}
    )

    post = Post.objects.get(object_uri=PAGE_URI)
    assert post.state == PostStates.fanned_out
    # a new post that mentions somebody here would notify them
    assert identity in post.mentions.all()
    stator.run_single_cycle()
    assert not FanOut.objects.filter(subject_post=post).exists()
    assert not TimelineEvent.objects.filter(identity=identity).exists()


@pytest.mark.django_db
@any_request
def test_fetch_announced_leaves_a_post_already_here_alone(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    _mock_origin(httpx_mock)
    Post.by_object_uri(PAGE_URI, fetch=True)

    Post.handle_fetch_announced(
        {"identity": followed_group.pk, "object": PAGE_URI, "id": ANNOUNCE_ID}
    )

    post = Post.objects.get(object_uri=PAGE_URI)
    assert post.state == PostStates.new
    assert PostInteraction.objects.filter(post=post, identity=followed_group).exists()


@pytest.mark.django_db
@any_request
def test_live_announce_does_reach_the_follower(
    httpx_mock: HTTPXMock, config_system, followed_group, identity, stator
):
    """The control for the test above: the same thread, announced live."""
    _mock_origin(httpx_mock)

    PostInteraction.handle_ap(_announce(PAGE_URI))
    stator.run_single_cycle()
    stator.run_single_cycle()

    post = Post.objects.get(object_uri=PAGE_URI)
    assert TimelineEvent.objects.filter(
        identity=identity, subject_post=post, type=TimelineEvent.Types.boost
    ).exists()


@pytest.mark.django_db
@any_request
def test_fetch_announced_does_not_list_a_comment(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    _mock_origin(httpx_mock)
    httpx_mock.add_response(
        url=COMMENT_URI,
        headers=AP_HEADERS,
        json={
            "@context": ["https://www.w3.org/ns/activitystreams"],
            "id": COMMENT_URI,
            "type": "Note",
            "attributedTo": AUTHOR_URI,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "inReplyTo": PAGE_URI,
            "content": "<p>A comment</p>",
            "published": format_ld_date(timezone.now() - timedelta(hours=1)),
        },
    )

    Post.handle_fetch_announced(
        {"identity": followed_group.pk, "object": COMMENT_URI, "id": ANNOUNCE_ID}
    )

    assert Post.objects.filter(object_uri=COMMENT_URI).exists()
    assert not PostInteraction.objects.exists()


@pytest.mark.django_db
def test_fetch_announced_drops_an_unreachable_object(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    httpx_mock.add_response(url=PAGE_URI, status_code=404)

    Post.handle_fetch_announced(
        {"identity": followed_group.pk, "object": PAGE_URI, "id": ANNOUNCE_ID}
    )

    assert not Post.objects.filter(object_uri=PAGE_URI).exists()


@pytest.mark.django_db
@any_request
def test_live_announce_after_backfill_keeps_one_boost(
    httpx_mock: HTTPXMock, config_system, followed_group
):
    _mock_origin(httpx_mock)
    Post.handle_fetch_announced(
        {"identity": followed_group.pk, "object": PAGE_URI, "id": ANNOUNCE_ID}
    )

    PostInteraction.handle_ap(
        _announce(PAGE_URI, "https://lemmy.test/activities/announce/2")
    )
    Post.handle_fetch_announced(
        {
            "identity": followed_group.pk,
            "object": PAGE_URI,
            "id": "https://lemmy.test/activities/announce/3",
        }
    )

    assert PostInteraction.objects.filter(type=PostInteraction.Types.boost).count() == 1


@pytest.mark.django_db
def test_accepted_follow_of_a_remote_group_queues_its_outbox(
    config_system, group, identity, remote_identity
):
    follow = Follow.objects.create(source=identity, target=group, state="accepting")
    person_follow = Follow.objects.create(
        source=identity, target=remote_identity, state="accepting"
    )

    assert FollowStates.handle_accepting(follow) == FollowStates.accepted
    assert FollowStates.handle_accepting(person_follow) == FollowStates.accepted

    assert _queued("FetchGroupOutbox") == [
        {"type": "FetchGroupOutbox", "identity": group.pk}
    ]
