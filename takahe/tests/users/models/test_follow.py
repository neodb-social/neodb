import json

import pytest
from pytest_httpx import HTTPXMock

from users.models import Follow, FollowStates, Identity, InboxMessage
from users.services import IdentityService


@pytest.mark.django_db
@pytest.mark.parametrize("ref_only", [True, False])
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_follow(
    identity: Identity,
    remote_identity: Identity,
    stator,
    httpx_mock: HTTPXMock,
    ref_only: bool,
):
    """
    Ensures that follow sending and acceptance works
    """
    # Make the follow
    follow = IdentityService(identity).follow(remote_identity)
    assert Follow.objects.get(pk=follow.pk).state == FollowStates.unrequested
    # Run stator to make it try and send out the remote request
    httpx_mock.add_response(
        url="https://remote.test/@test/inbox/",
        status_code=202,
    )
    stator.run_single_cycle()
    outbound_data = json.loads(httpx_mock.get_request().content)
    assert outbound_data["type"] == "Follow"
    assert outbound_data["actor"] == identity.actor_uri
    assert outbound_data["object"] == remote_identity.actor_uri
    assert outbound_data["id"] == f"{identity.actor_uri}follow/{follow.pk}/"
    assert Follow.objects.get(pk=follow.pk).state == FollowStates.pending_approval
    # Come in with an inbox message of either a reference type or an embedded type
    if ref_only:
        message = {
            "type": "Accept",
            "id": "test",
            "actor": remote_identity.actor_uri,
            "object": outbound_data["id"],
        }
    else:
        del outbound_data["@context"]
        message = {
            "type": "Accept",
            "id": "test",
            "actor": remote_identity.actor_uri,
            "object": outbound_data,
        }
    InboxMessage.objects.create(message=message)
    # Run stator and ensure that accepted our follow
    stator.run_single_cycle()
    stator.run_single_cycle()
    assert Follow.objects.get(pk=follow.pk).state == FollowStates.accepted


def _stats(i: Identity) -> dict:
    return Identity.objects.get(pk=i.pk).stats or {}


@pytest.mark.django_db
def test_follow_counts_track_state_changes(
    identity: Identity,
    other_identity: Identity,
    stator,
):
    """#1616: stats must track follow, accept, unfollow and re-follow."""
    # Following is an active outbound follow, but not yet an accepted follower.
    IdentityService(identity).follow(other_identity)
    assert _stats(identity).get("following_count") == 1
    assert _stats(other_identity).get("followers_count") == 0

    # Stator accepts the local follow.
    stator.run_single_cycle()
    stator.run_single_cycle()
    assert (
        Follow.objects.get(source=identity, target=other_identity).state
        == FollowStates.accepted
    )
    assert _stats(identity).get("following_count") == 1
    assert _stats(other_identity).get("followers_count") == 1

    # Unfollowing drops both counts before the row is asynchronously removed.
    IdentityService(identity).unfollow(other_identity)
    assert Follow.objects.filter(source=identity, target=other_identity).exists()
    assert _stats(identity).get("following_count") == 0
    assert _stats(other_identity).get("followers_count") == 0

    # Re-following reuses the still-present inactive row.
    IdentityService(identity).follow(other_identity)
    assert _stats(identity).get("following_count") == 1


@pytest.mark.django_db
def test_calculate_stats_ignores_inactive_follows(
    identity: Identity,
    identity_factory,
):
    """#1616: calculate_stats counts only active outbound and accepted inbound."""
    followee_active = identity_factory(username="followeeactive")
    followee_undone = identity_factory(username="followeeundone")
    follower_accepted = identity_factory(username="followeraccepted")
    follower_pending = identity_factory(username="followerpending")

    Follow.objects.create(
        source=identity, target=followee_active, state=FollowStates.accepted
    )
    Follow.objects.create(
        source=identity, target=followee_undone, state=FollowStates.undone
    )
    Follow.objects.create(
        source=follower_accepted, target=identity, state=FollowStates.accepted
    )
    Follow.objects.create(
        source=follower_pending, target=identity, state=FollowStates.pending_approval
    )

    identity.calculate_stats()
    stats = _stats(identity)
    assert stats["following_count"] == 1
    assert stats["followers_count"] == 1
