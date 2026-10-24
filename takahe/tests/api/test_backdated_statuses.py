import datetime

import pytest
from django.utils import timezone

from activities.models import (
    FanOut,
    Post,
    PostStates,
    ScheduledStatus,
    TimelineEvent,
)
from core.ld import format_ld_date

PAST = datetime.datetime(2019, 5, 4, 3, 2, 1, tzinfo=datetime.UTC)


def _post(api_client, **data):
    return api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={"scheduled_at": PAST.isoformat(), **data},
    )


@pytest.mark.django_db
def test_past_time_posts_now_when_backdating_is_off(
    api_client, identity, config_system
):
    response = _post(api_client, status="now").json()
    post = Post.objects.get(pk=response["id"])
    assert post.published > timezone.now() - datetime.timedelta(minutes=1)
    assert TimelineEvent.objects.filter(subject_post=post).exists()


@pytest.mark.django_db
def test_backdated_post(api_client, identity, other_identity, config_system):
    config_system.post_backdating = True
    response = _post(api_client, status="from the archive #old").json()
    assert response["created_at"] == format_ld_date(PAST)
    post = Post.objects.get(pk=response["id"])
    assert post.published == PAST
    # Starts out fanned out, so stator never federates or notifies it
    assert post.state == PostStates.fanned_out
    assert post.hashtags == ["old"]
    assert not FanOut.objects.filter(subject_post=post).exists()
    assert not TimelineEvent.objects.filter(subject_post=post).exists()
    assert not ScheduledStatus.objects.exists()

    # A reply to one's own backdated post is fine
    reply = _post(api_client, status="thread", in_reply_to_id=str(post.pk))
    assert reply.status_code == 200
    assert Post.objects.get(pk=reply.json()["id"]).in_reply_to == post.object_uri


@pytest.mark.django_db
def test_backdating_refuses_anything_involving_others(
    api_client, identity, other_identity, config_system
):
    config_system.post_backdating = True
    theirs = Post.create_local(author=other_identity, content="theirs")
    cases = [
        ({"status": f"hi @{other_identity.handle}"}, "mentioning others"),
        ({"status": "re", "in_reply_to_id": str(theirs.pk)}, "Replies to others"),
        ({"status": "q", "quoted_status_id": str(theirs.pk)}, "Quote posts"),
        (
            {"status": "p", "poll": {"options": ["a", "b"], "expires_in": 3600}},
            "polls",
        ),
        (
            {"status": "epoch", "scheduled_at": "1970-01-01T00:00:00Z"},
            "Unix epoch",
        ),
    ]
    for data, message in cases:
        response = _post(api_client, **data)
        assert response.status_code == 422, data
        assert message in response.json()["error"], data
    assert not Post.objects.filter(author=identity).exists()


@pytest.mark.django_db
def test_backdate_from_compose(identity, client_with_user, config_system):
    config_system.post_backdating = True
    response = client_with_user.post(
        f"/@{identity.handle}/compose/",
        data={
            "text": "old news",
            "visibility": "0",
            "scheduled_at": "2019-05-04T12:30",
            "browser_timezone": "Europe/Paris",
        },
    )
    assert response.status_code == 302
    post = identity.posts.get()
    assert post.published == datetime.datetime(2019, 5, 4, 10, 30, tzinfo=datetime.UTC)
    assert post.state == PostStates.fanned_out
    assert not TimelineEvent.objects.filter(subject_post=post).exists()


@pytest.mark.django_db
def test_compose_refuses_past_time_when_backdating_is_off(
    identity, client_with_user, config_system
):
    response = client_with_user.post(
        f"/@{identity.handle}/compose/",
        data={
            "text": "old news",
            "visibility": "0",
            "scheduled_at": "2019-05-04T12:30",
            "browser_timezone": "Europe/Paris",
        },
    )
    assert response.status_code == 200
    assert b"date must be in the future" in response.content
    assert not identity.posts.exists()
