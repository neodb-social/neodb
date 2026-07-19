from datetime import timedelta

import pytest
from django.utils import timezone
from pytest_httpx import HTTPXMock

from activities.models import (
    FanOut,
    Post,
    PostInteraction,
    PostStates,
    TimelineEvent,
)
from activities.models.post_types import QuestionData
from core.ld import format_ld_date
from users.models import Identity


def make_local_poll(author, mode="oneOf", expires=timedelta(days=1), hide_totals=False):
    return Post.create_local(
        author=author,
        content="<p>Test Question</p>",
        question={
            "type": "Question",
            "mode": mode,
            "options": [
                {"name": "Option 1", "type": "Note", "votes": 0},
                {"name": "Option 2", "type": "Note", "votes": 0},
            ],
            "voter_count": 0,
            "hide_totals": hide_totals,
            "end_time": format_ld_date(timezone.now() + expires),
        },
    )


def expire_poll(post):
    post.type_data.end_time = timezone.now() - timedelta(hours=1)
    post.save()


def edited_fan_outs(post):
    return FanOut.objects.filter(subject_post=post, type=FanOut.Types.post_edited)


@pytest.mark.django_db
def test_new_local_poll_enters_question_open(identity: Identity, config_system):
    post = make_local_poll(identity)
    assert post.type_data.last_distributed_tally == "0:0:0"
    assert PostStates.handle_new(post) == PostStates.question_open


@pytest.mark.django_db
def test_new_remote_question_stays_fanned_out(remote_identity: Identity, config_system):
    post = Post.objects.create(
        author=remote_identity,
        local=False,
        content="<p>Test Question</p>",
        object_uri="https://remote.test/status/poll-new",
        type=Post.Types.question,
        type_data={
            "type": "Question",
            "mode": "oneOf",
            "options": [
                {"name": "Option 1", "type": "Note", "votes": 0},
                {"name": "Option 2", "type": "Note", "votes": 0},
            ],
            "voter_count": 0,
            "end_time": format_ld_date(timezone.now() + timedelta(1)),
        },
    )
    post.refresh_from_db()
    assert PostStates.handle_new(post) == PostStates.fanned_out


@pytest.mark.django_db
def test_question_open_distributes_tally_updates(
    identity: Identity, identity2: Identity, config_system
):
    post = make_local_poll(identity)
    # No votes yet: nothing to distribute, stays in question_open
    assert PostStates.handle_question_open(post) is None
    assert not edited_fan_outs(post).exists()

    PostInteraction.create_votes(post, identity2, [0])
    post.refresh_from_db()
    assert PostStates.handle_question_open(post) is None
    assert edited_fan_outs(post).filter(identity=identity2).exists()
    post.refresh_from_db()
    assert post.type_data.last_distributed_tally == "1:1:0"

    # Unchanged tallies do not fan out again
    edited_fan_outs(post).delete()
    post.refresh_from_db()
    assert PostStates.handle_question_open(post) is None
    assert not edited_fan_outs(post).exists()


@pytest.mark.django_db
def test_question_open_hide_totals(
    identity: Identity, identity2: Identity, config_system
):
    post = make_local_poll(identity, hide_totals=True)
    PostInteraction.create_votes(post, identity2, [0])
    post.refresh_from_db()
    # Hidden totals: no tally Updates while the poll is running
    assert PostStates.handle_question_open(post) is None
    assert not edited_fan_outs(post).exists()
    # Per-option tallies are hidden in the API and over AP
    assert post.type_data.to_mastodon_json(post)["options"][0]["votes_count"] is None
    ap = post.to_ap()
    assert ap["oneOf"][0]["replies"]["totalItems"] == 0

    # The final Update after expiry reveals the tallies
    expire_poll(post)
    post.refresh_from_db()
    assert PostStates.handle_question_open(post) == PostStates.fanned_out
    assert edited_fan_outs(post).exists()
    post.refresh_from_db()
    assert post.type_data.to_mastodon_json(post)["options"][0]["votes_count"] == 1
    assert post.to_ap()["oneOf"][0]["replies"]["totalItems"] == 1


@pytest.mark.django_db
def test_question_open_expiry_notifies_and_closes(
    identity: Identity, identity2: Identity, config_system
):
    post = make_local_poll(identity)
    PostInteraction.create_votes(post, identity2, [1])
    post.refresh_from_db()
    expire_poll(post)
    post.refresh_from_db()

    assert PostStates.handle_question_open(post) == PostStates.fanned_out
    assert edited_fan_outs(post).exists()
    # Both the author and the local voter get a poll-ended notification
    assert TimelineEvent.objects.filter(
        identity=identity, type=TimelineEvent.Types.poll, subject_post=post
    ).exists()
    assert TimelineEvent.objects.filter(
        identity=identity2, type=TimelineEvent.Types.poll, subject_post=post
    ).exists()
    # The final AP representation carries `closed`
    ap = post.to_ap()
    assert ap["closed"] == ap["endTime"]


@pytest.mark.django_db
def test_remote_poll_vote_tracks_expiry(
    identity: Identity, remote_identity: Identity, config_system
):
    post = Post.objects.create(
        author=remote_identity,
        local=False,
        content="<p>Test Question</p>",
        object_uri="https://remote.test/status/poll-track",
        state=PostStates.fanned_out,
        type=Post.Types.question,
        type_data={
            "type": "Question",
            "mode": "oneOf",
            "options": [
                {"name": "Option 1", "type": "Note", "votes": 3},
                {"name": "Option 2", "type": "Note", "votes": 4},
            ],
            "voter_count": 7,
            "end_time": format_ld_date(timezone.now() + timedelta(1)),
        },
    )
    post.refresh_from_db()
    PostInteraction.create_votes(post, identity, [0])
    post.refresh_from_db()
    # Voting on a remote poll starts expiry tracking
    assert post.state == str(PostStates.question_open)

    # Not expired yet: stays put and does not notify
    assert PostStates.handle_question_open(post) is None

    expire_poll(post)
    post.refresh_from_db()
    assert PostStates.handle_question_open(post) == PostStates.fanned_out
    assert TimelineEvent.objects.filter(
        identity=identity, type=TimelineEvent.Types.poll, subject_post=post
    ).exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_refresh_question_from_remote(
    httpx_mock: HTTPXMock, identity: Identity, remote_identity: Identity, config_system
):
    post = Post.objects.create(
        author=remote_identity,
        local=False,
        content="<p>Test Question</p>",
        object_uri="https://remote.test/status/poll-refresh",
        state=PostStates.fanned_out,
        type=Post.Types.question,
        type_data={
            "type": "Question",
            "mode": "oneOf",
            "options": [
                {"name": "Option 1", "type": "Note", "votes": 3},
                {"name": "Option 2", "type": "Note", "votes": 4},
            ],
            "voter_count": 7,
            "end_time": format_ld_date(timezone.now() + timedelta(1)),
        },
    )
    post.refresh_from_db()
    httpx_mock.add_response(
        url="https://remote.test/status/poll-refresh",
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": [
                "https://www.w3.org/ns/activitystreams",
                {
                    "toot": "http://joinmastodon.org/ns#",
                    "votersCount": "toot:votersCount",
                },
            ],
            "id": "https://remote.test/status/poll-refresh",
            "type": "Question",
            "attributedTo": remote_identity.actor_uri,
            "content": "<p>Test Question</p>",
            "endTime": format_ld_date(timezone.now() + timedelta(1)),
            "votersCount": 12,
            "oneOf": [
                {
                    "name": "Option 1",
                    "type": "Note",
                    "replies": {"type": "Collection", "totalItems": 5},
                },
                {
                    "name": "Option 2",
                    "type": "Note",
                    "replies": {"type": "Collection", "totalItems": 7},
                },
            ],
        },
    )
    refreshed = post.refresh_question_if_stale()
    assert isinstance(refreshed.type_data, QuestionData)
    assert refreshed.type_data.voter_count == 12
    assert refreshed.type_data.options[0].votes == 5
    assert refreshed.type_data.last_fetched is not None
    # A fresh fetch within a minute is skipped (no second request mocked)
    assert refreshed.refresh_question_if_stale().type_data.voter_count == 12


@pytest.mark.django_db
def test_vote_on_remote_poll_with_long_option(
    identity: Identity, remote_identity: Identity, config_system
):
    long_name = "A" * 80
    post = Post.objects.create(
        author=remote_identity,
        local=False,
        content="<p>Test Question</p>",
        object_uri="https://remote.test/status/poll-long",
        state=PostStates.fanned_out,
        type=Post.Types.question,
        type_data={
            "type": "Question",
            "mode": "oneOf",
            "options": [
                {"name": long_name, "type": "Note", "votes": 0},
                {"name": "Short", "type": "Note", "votes": 0},
            ],
            "voter_count": 0,
            "end_time": format_ld_date(timezone.now() + timedelta(1)),
        },
    )
    post.refresh_from_db()
    vote = PostInteraction.create_votes(post, identity, [0])[0]
    # The stored value fits the 50-char column, and own_votes still resolve
    assert vote.value == long_name[:50]
    json = post.type_data.to_mastodon_json(post, identity=identity)
    assert json["own_votes"] == [0]


@pytest.mark.django_db
def test_undone_votes_are_not_counted(
    identity: Identity, identity2: Identity, config_system
):
    from activities.models import PostInteractionStates

    post = make_local_poll(identity)
    vote = PostInteraction.create_votes(post, identity2, [0])[0]
    post.refresh_from_db()
    assert post.type_data.options[0].votes == 1

    # e.g. a block or an AP Undo forces the vote out of the active states
    vote.transition_perform(PostInteractionStates.undone_fanned_out)
    post.calculate_type_data()
    post.refresh_from_db()
    assert post.type_data.options[0].votes == 0
    assert post.type_data.voter_count == 0
    assert post.question_local_voters() == []


@pytest.mark.django_db
def test_update_ap_ids_are_unique_per_revision(identity: Identity, config_system):
    post = make_local_poll(identity)
    first = post.to_update_ap()["id"]
    assert "#updates/" in first
