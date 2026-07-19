from datetime import timedelta

import pytest
from django.utils import timezone

from activities.models import Post, PostInteraction, TimelineEvent
from activities.models.post_types import POLL_MAX_OPTIONS
from core.ld import format_ld_date


def make_poll_post(author, mode="oneOf", hide_totals=False, expires=timedelta(1)):
    return Post.create_local(
        author=author,
        content="<p>Test Question</p>",
        question={
            "type": "Question",
            "mode": mode,
            "options": [
                {"name": "Option 1", "type": "Note", "votes": 0},
                {"name": "Option 2", "type": "Note", "votes": 0},
                {"name": "Option 3", "type": "Note", "votes": 0},
            ],
            "voter_count": 0,
            "hide_totals": hide_totals,
            "end_time": format_ld_date(timezone.now() + expires),
        },
    )


def vote(api_client, post_id, choices):
    return api_client.post(
        f"/api/v1/polls/{post_id}/votes",
        content_type="application/json",
        data={"choices": choices},
    )


@pytest.mark.django_db
def test_get_poll(api_client):
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": "Hello, world!",
            "poll": {
                "options": ["Option 1", "Option 2"],
                "expires_in": 300,
            },
        },
    ).json()

    id = response["id"]

    response = api_client.get(
        f"/api/v1/polls/{id}",
    ).json()

    assert response["id"] == id
    assert response["voted"]


@pytest.mark.django_db
def test_vote_poll(api_client, identity2):
    post = make_poll_post(identity2)

    response = vote(api_client, post.id, [0]).json()

    assert response["id"] == str(post.id)
    assert response["voted"]
    assert response["votes_count"] == 1
    assert response["own_votes"] == [0]


@pytest.mark.django_db
def test_vote_poll_own_poll_rejected(api_client, identity):
    post = make_poll_post(identity)
    response = vote(api_client, post.id, [0])
    assert response.status_code == 422
    assert "own poll" in response.json()["error"]


@pytest.mark.django_db
def test_vote_poll_invalid_choice(api_client, identity2):
    post = make_poll_post(identity2)
    response = vote(api_client, post.id, [3])
    assert response.status_code == 422
    assert "does not exist" in response.json()["error"]


@pytest.mark.django_db
def test_vote_poll_no_choices(api_client, identity2):
    post = make_poll_post(identity2)
    response = vote(api_client, post.id, [])
    assert response.status_code == 422


@pytest.mark.django_db
def test_vote_poll_twice_rejected(api_client, identity2):
    post = make_poll_post(identity2)
    assert vote(api_client, post.id, [0]).status_code == 200
    response = vote(api_client, post.id, [1])
    assert response.status_code == 422
    assert "already voted" in response.json()["error"]


@pytest.mark.django_db
def test_vote_poll_single_choice_multiple_votes_rejected(api_client, identity2):
    post = make_poll_post(identity2)
    response = vote(api_client, post.id, [0, 1])
    assert response.status_code == 422


@pytest.mark.django_db
def test_vote_poll_multiple_additive(api_client, identity2):
    post = make_poll_post(identity2, mode="anyOf")
    assert vote(api_client, post.id, [0]).status_code == 200
    # Adding a different choice later is allowed on multiple-choice polls
    response = vote(api_client, post.id, [1]).json()
    assert sorted(response["own_votes"]) == [0, 1]
    assert response["votes_count"] == 2
    assert response["voters_count"] == 1
    # Re-voting an already chosen option is not
    assert vote(api_client, post.id, [1]).status_code == 422


@pytest.mark.django_db
def test_vote_poll_expired(api_client, identity2):
    post = make_poll_post(identity2, expires=timedelta(hours=-1))
    response = vote(api_client, post.id, [0])
    assert response.status_code == 422
    assert "already ended" in response.json()["error"]


@pytest.mark.django_db
def test_poll_hide_totals(api_client, identity2):
    post = make_poll_post(identity2, hide_totals=True)
    response = vote(api_client, post.id, [0]).json()
    assert response["options"][0]["votes_count"] is None
    assert response["votes_count"] == 1
    assert response["own_votes"] == [0]

    # Once the poll ends, the tallies are revealed
    post.refresh_from_db()
    post.type_data.end_time = timezone.now() - timedelta(hours=1)
    post.save()
    response = api_client.get(f"/api/v1/polls/{post.id}").json()
    assert response["expired"]
    assert response["options"][0]["votes_count"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "poll,error",
    [
        ({"options": ["A"], "expires_in": 300}, "more than one item"),
        (
            {
                "options": [f"O{i}" for i in range(POLL_MAX_OPTIONS + 1)],
                "expires_in": 300,
            },
            "can't contain more than",
        ),
        ({"options": ["A", "A"], "expires_in": 300}, "duplicate"),
        ({"options": ["A", "  "], "expires_in": 300}, "blank"),
        ({"options": ["A", "B" * 51], "expires_in": 300}, "characters each"),
        ({"options": ["A", "B"], "expires_in": 60}, "too soon"),
        ({"options": ["A", "B"], "expires_in": 3000000}, "too far into the future"),
    ],
)
def test_create_poll_validation(api_client, poll, error):
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={"status": "Poll!", "poll": poll},
    )
    assert response.status_code == 422
    assert error in response.json()["error"]


@pytest.mark.django_db
def test_create_poll_max_options_allowed(api_client):
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": "Poll!",
            "poll": {
                "options": [f"O{i}" for i in range(POLL_MAX_OPTIONS)],
                "expires_in": 300,
            },
        },
    )
    assert response.status_code == 200
    assert len(response.json()["poll"]["options"]) == POLL_MAX_OPTIONS


@pytest.mark.django_db
def test_create_poll_with_media_rejected(api_client):
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": "Poll!",
            "media_ids": ["12345"],
            "poll": {"options": ["A", "B"], "expires_in": 300},
        },
    )
    assert response.status_code == 422


@pytest.mark.django_db
def test_edit_poll(api_client, identity, identity2):
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": "Poll!",
            "poll": {"options": ["A", "B"], "expires_in": 3600},
        },
    ).json()
    post = Post.objects.get(pk=response["id"])
    PostInteraction.create_votes(post, identity2, [0])
    post.refresh_from_db()
    assert post.type_data.options[0].votes == 1

    # Editing without changing the options keeps the votes
    response = api_client.put(
        f"/api/v1/statuses/{post.id}",
        content_type="application/json",
        data={
            "status": "Poll! (edited)",
            "poll": {"options": ["A", "B"], "expires_in": 3600},
        },
    ).json()
    assert response["poll"]["votes_count"] == 1

    # Changing the options resets all votes
    response = api_client.put(
        f"/api/v1/statuses/{post.id}",
        content_type="application/json",
        data={
            "status": "Poll! (edited again)",
            "poll": {"options": ["X", "Y", "Z"], "expires_in": 3600},
        },
    ).json()
    assert response["poll"]["votes_count"] == 0
    assert [o["title"] for o in response["poll"]["options"]] == ["X", "Y", "Z"]
    post.refresh_from_db()
    assert not post.interactions.filter(type=PostInteraction.Types.vote).exists()

    # Removing the poll turns the post back into a plain note
    response = api_client.put(
        f"/api/v1/statuses/{post.id}",
        content_type="application/json",
        data={"status": "No poll anymore"},
    ).json()
    assert response["poll"] is None
    post.refresh_from_db()
    assert post.type == Post.Types.note


@pytest.mark.django_db
def test_poll_notification(api_client, identity, identity2):
    post = make_poll_post(identity2)
    TimelineEvent.add_poll_ended(identity, post)

    response = api_client.get(
        "/api/v1/notifications",
        data={"types[]": ["poll"]},
    ).json()
    assert len(response) == 1
    assert response[0]["type"] == "poll"
    assert response[0]["status"]["id"] == str(post.id)
    assert response[0]["account"]["id"] == str(identity2.id)


@pytest.mark.django_db
def test_poll_visibility_respected(api_client, identity2):
    post = Post.create_local(
        author=identity2,
        content="<p>Followers only poll</p>",
        visibility=Post.Visibilities.followers,
        question={
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
    # The API identity does not follow identity2, so the poll is invisible
    assert api_client.get(f"/api/v1/polls/{post.id}").status_code == 404
    assert vote(api_client, post.id, [0]).status_code == 404
