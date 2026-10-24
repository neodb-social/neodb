import datetime

import pytest
from django.utils import timezone

from activities.models import (
    Post,
    PostAttachment,
    PostAttachmentStates,
    ScheduledStatus,
    ScheduledStatusStates,
)
from core.ld import format_ld_date


def _in(**kwargs) -> datetime.datetime:
    return timezone.now() + datetime.timedelta(**kwargs)


def _make_due(scheduled: ScheduledStatus) -> None:
    ScheduledStatus.objects.filter(pk=scheduled.pk).update(
        scheduled_at=_in(seconds=-1), state_next_attempt=_in(seconds=-1)
    )


@pytest.mark.django_db
def test_schedule_list_update_delete(api_client, identity):
    attachment = PostAttachment.objects.create(
        mimetype="image/webp", state=PostAttachmentStates.fetched, author=identity
    )
    when = _in(days=1)
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": "Hello later",
            "visibility": "private",
            "spoiler_text": "cw",
            "media_ids": [str(attachment.pk)],
            "scheduled_at": when.isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scheduled_at"] == format_ld_date(when)
    assert data["params"]["text"] == "Hello later"
    assert data["params"]["visibility"] == "private"
    assert data["params"]["spoiler_text"] == "cw"
    assert data["params"]["media_ids"] == [str(attachment.pk)]
    assert data["params"]["scheduled_at"] is None
    assert data["media_attachments"][0]["id"] == str(attachment.pk)
    assert not Post.objects.filter(author=identity).exists()
    scheduled = ScheduledStatus.objects.get(pk=data["id"])
    # Stator only picks the row up once it is due
    assert scheduled.state_next_attempt == scheduled.scheduled_at

    listing = api_client.get("/api/v1/scheduled_statuses").json()
    assert [s["id"] for s in listing] == [data["id"]]
    assert api_client.get(f"/api/v1/scheduled_statuses/{data['id']}").json() == data

    later = _in(days=2)
    response = api_client.put(
        f"/api/v1/scheduled_statuses/{data['id']}",
        content_type="application/json",
        data={"scheduled_at": later.isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["scheduled_at"] == format_ld_date(later)
    scheduled.refresh_from_db()
    assert scheduled.state_next_attempt == later

    response = api_client.put(
        f"/api/v1/scheduled_statuses/{data['id']}",
        content_type="application/json",
        data={"scheduled_at": _in(minutes=1).isoformat()},
    )
    assert response.status_code == 422

    response = api_client.delete(f"/api/v1/scheduled_statuses/{data['id']}")
    assert response.status_code == 200
    assert response.json() == {}
    assert not ScheduledStatus.objects.exists()
    response = api_client.get(f"/api/v1/scheduled_statuses/{data['id']}")
    assert response.status_code == 404


@pytest.mark.django_db
def test_schedule_in_the_past_posts_now(api_client, identity):
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={"status": "now", "scheduled_at": _in(minutes=-1).isoformat()},
    ).json()
    assert response["content"] == "<p>now</p>"
    assert not ScheduledStatus.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "scheduled_at,message",
    [
        ("tomorrow", "Scheduled at is invalid"),
        (None, "date must be in the future"),
    ],
)
def test_schedule_rejects_bad_times(api_client, identity, scheduled_at, message):
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": "soon",
            "scheduled_at": scheduled_at or _in(minutes=2).isoformat(),
        },
    )
    assert response.status_code == 422
    assert message in response.json()["error"]
    assert not ScheduledStatus.objects.exists()


@pytest.mark.django_db
def test_schedule_accepts_naive_and_date_only_values(api_client, identity):
    day = (timezone.now() + datetime.timedelta(days=3)).date()
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={"status": "a", "scheduled_at": day.isoformat()},
    ).json()
    assert response["scheduled_at"] == f"{day.isoformat()}T00:00:00.000Z"


@pytest.mark.django_db
def test_schedule_limits(api_client, identity, monkeypatch):
    monkeypatch.setattr(ScheduledStatus, "DAILY_LIMIT", 2)
    monkeypatch.setattr(ScheduledStatus, "TOTAL_LIMIT", 3)
    day = _in(days=2).replace(hour=12)

    def schedule(when: datetime.datetime) -> int:
        return api_client.post(
            "/api/v1/statuses",
            content_type="application/json",
            data={"status": "x", "scheduled_at": when.isoformat()},
        ).status_code

    assert schedule(day) == 200
    assert schedule(day + datetime.timedelta(hours=1)) == 200
    assert schedule(day + datetime.timedelta(hours=2)) == 422
    assert schedule(day + datetime.timedelta(days=1)) == 200
    assert schedule(day + datetime.timedelta(days=2)) == 422


@pytest.mark.django_db
def test_schedule_refuses_media_already_posted(api_client, identity):
    post = Post.create_local(author=identity, content="posted")
    attachment = PostAttachment.objects.create(
        mimetype="image/webp",
        state=PostAttachmentStates.fetched,
        author=identity,
        post=post,
    )
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": "again",
            "media_ids": [str(attachment.pk)],
            "scheduled_at": _in(days=1).isoformat(),
        },
    )
    assert response.status_code == 422


@pytest.mark.django_db
def test_scheduled_status_is_private_to_its_author(
    api_client, identity, other_identity
):
    scheduled = ScheduledStatus.create(
        author=other_identity, scheduled_at=_in(days=1), text="mine"
    )
    assert api_client.get("/api/v1/scheduled_statuses").json() == []
    url = f"/api/v1/scheduled_statuses/{scheduled.pk}"
    assert api_client.get(url).status_code == 404
    assert api_client.delete(url).status_code == 404
    assert ScheduledStatus.objects.filter(pk=scheduled.pk).exists()


@pytest.mark.django_db
def test_stator_publishes_when_due(api_client, identity, stator):
    attachment = PostAttachment.objects.create(
        mimetype="image/webp", state=PostAttachmentStates.fetched, author=identity
    )
    data = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": "poll time",
            "visibility": "unlisted",
            "poll": {"options": ["a", "b"], "expires_in": 3600},
            "scheduled_at": _in(days=1).isoformat(),
        },
    ).json()
    scheduled = ScheduledStatus.objects.get(pk=data["id"])

    # Not due yet: nothing happens
    stator.run_single_cycle()
    assert ScheduledStatus.objects.filter(pk=scheduled.pk).exists()
    assert not Post.objects.filter(author=identity).exists()

    _make_due(scheduled)
    stator.run_single_cycle()
    assert not ScheduledStatus.objects.filter(pk=scheduled.pk).exists()
    post = Post.objects.get(author=identity)
    assert post.content == "<p>poll time</p>"
    assert post.visibility == Post.Visibilities.unlisted
    assert post.application == scheduled.application
    assert post.type == Post.Types.question
    # The poll runs for expires_in from the time it posts
    end_time = post.type_data.end_time
    assert _in(minutes=59) < end_time < _in(minutes=61)
    attachment.refresh_from_db()
    assert attachment.post is None


@pytest.mark.django_db
def test_publish_attaches_media(identity, config_system):
    attachment = PostAttachment.objects.create(
        mimetype="image/webp", state=PostAttachmentStates.fetched, author=identity
    )
    scheduled = ScheduledStatus.create(
        author=identity,
        scheduled_at=_in(days=1),
        text="",
        media_ids=[str(attachment.pk)],
    )
    _make_due(scheduled)
    scheduled.refresh_from_db()
    assert (
        ScheduledStatusStates.handle_scheduled(scheduled)
        == ScheduledStatusStates.published
    )
    post = Post.objects.get(author=identity)
    assert list(post.attachments.all()) == [attachment]


@pytest.mark.django_db
def test_publish_keeps_local_only(identity, config_system):
    scheduled = ScheduledStatus.create(
        author=identity,
        scheduled_at=_in(days=1),
        text="locals",
        visibility=Post.Visibilities.local_only,
    )
    assert scheduled.params["visibility"] == "public"
    post = scheduled.publish()
    assert post is not None
    assert post.visibility == Post.Visibilities.local_only


@pytest.mark.django_db
def test_publish_drops_a_reply_to_a_deleted_post(identity, config_system):
    parent = Post.create_local(author=identity, content="parent")
    scheduled = ScheduledStatus.create(
        author=identity,
        scheduled_at=_in(days=1),
        text="reply",
        in_reply_to_id=str(parent.pk),
    )
    assert scheduled.params["in_reply_to_id"] == parent.pk
    parent.delete()
    assert scheduled.publish() is None
    assert not ScheduledStatus.objects.exists()
    assert not Post.objects.filter(content__contains="reply").exists()


@pytest.mark.django_db
def test_publish_skips_a_deleted_author(identity, config_system):
    scheduled = ScheduledStatus.create(
        author=identity, scheduled_at=_in(days=1), text="gone"
    )
    identity.deleted = timezone.now()
    identity.save()
    scheduled.refresh_from_db()
    assert scheduled.publish() is None
    assert not Post.objects.filter(author=identity).exists()


@pytest.mark.django_db
def test_publish_after_cancel_posts_nothing(identity, config_system):
    scheduled = ScheduledStatus.create(
        author=identity, scheduled_at=_in(days=1), text="cancelled"
    )
    ScheduledStatus.objects.filter(pk=scheduled.pk).delete()
    assert scheduled.publish() is None
    assert not Post.objects.filter(author=identity).exists()
