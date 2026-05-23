import pytest

from activities.models import Post, TimelineEvent
from users.models import AccountNote
from users.models.block import Block


@pytest.mark.django_db
def test_mutes_empty(api_client):
    response = api_client.get("/api/v1/mutes")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.django_db
def test_mutes_list(api_client, identity, other_identity):
    Block.create_local_mute(source=identity, target=other_identity)
    response = api_client.get("/api/v1/mutes")
    assert response.status_code == 200
    result = response.json()
    assert len(result) == 1
    assert result[0]["id"] == str(other_identity.pk)


@pytest.mark.django_db
def test_mutes_excludes_blocks(api_client, identity, other_identity):
    Block.create_local_block(source=identity, target=other_identity)
    response = api_client.get("/api/v1/mutes")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.django_db
def test_instance_rules(api_client):
    response = api_client.get("/api/v1/instance/rules")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.django_db
def test_status_mute(api_client, identity):
    post = Post.create_local(author=identity, content="Hello")
    response = api_client.post(f"/api/v1/statuses/{post.pk}/mute")
    assert response.status_code == 200
    assert response.json()["muted"] is True


@pytest.mark.django_db
def test_status_unmute(api_client, identity):
    post = Post.create_local(author=identity, content="Hello")
    response = api_client.post(f"/api/v1/statuses/{post.pk}/unmute")
    assert response.status_code == 200
    assert response.json()["muted"] is False


@pytest.mark.django_db
def test_notifications_unread_count(api_client, identity, other_identity):
    response = api_client.get("/api/v1/notifications/unread_count")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert data["count"] == 0


@pytest.mark.django_db
def test_notifications_unread_count_with_notifications(
    api_client, identity, other_identity
):
    TimelineEvent.objects.create(
        identity=identity,
        type=TimelineEvent.Types.mentioned,
        subject_identity=other_identity,
    )
    response = api_client.get("/api/v1/notifications/unread_count")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db
def test_notifications_unread_count_with_limit(api_client, identity, other_identity):
    for _ in range(5):
        TimelineEvent.objects.create(
            identity=identity,
            type=TimelineEvent.Types.mentioned,
            subject_identity=other_identity,
        )
    response = api_client.get("/api/v1/notifications/unread_count?limit=3")
    assert response.status_code == 200
    assert response.json()["count"] == 3


@pytest.mark.django_db
def test_account_note_set(api_client, identity, other_identity):
    response = api_client.post(
        f"/api/v1/accounts/{other_identity.pk}/note",
        content_type="application/json",
        data={"comment": "This is my private note"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["note"] == "This is my private note"
    assert AccountNote.objects.filter(source=identity, target=other_identity).exists()


@pytest.mark.django_db
def test_account_note_update(api_client, identity, other_identity):
    AccountNote.objects.create(source=identity, target=other_identity, note="Old note")
    response = api_client.post(
        f"/api/v1/accounts/{other_identity.pk}/note",
        content_type="application/json",
        data={"comment": "New note"},
    )
    assert response.status_code == 200
    assert response.json()["note"] == "New note"
    assert (
        AccountNote.objects.get(source=identity, target=other_identity).note
        == "New note"
    )


@pytest.mark.django_db
def test_account_note_clear(api_client, identity, other_identity):
    AccountNote.objects.create(source=identity, target=other_identity, note="My note")
    response = api_client.post(
        f"/api/v1/accounts/{other_identity.pk}/note",
        content_type="application/json",
        data={"comment": ""},
    )
    assert response.status_code == 200
    assert response.json()["note"] == ""


@pytest.mark.django_db
def test_account_note_in_relationship(api_client, identity, other_identity):
    AccountNote.objects.create(source=identity, target=other_identity, note="My note")
    response = api_client.get(
        f"/api/v1/accounts/relationships?id[]={other_identity.pk}"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["note"] == "My note"
