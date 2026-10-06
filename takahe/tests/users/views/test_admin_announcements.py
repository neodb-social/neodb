import pytest
from django.test import Client

from users.models import Announcement, User


@pytest.fixture
def announcement(db) -> Announcement:
    return Announcement.objects.create(text="Hello", published=False)


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["publish", "unpublish"])
def test_announcement_actions_need_admin(
    client: Client, user: User, announcement: Announcement, action: str
):
    announcement.published = action == "unpublish"
    announcement.save()
    url = f"/admin/announcements/{announcement.pk}/{action}/"

    response = client.post(url)
    assert response.status_code == 302
    client.force_login(user)
    response = client.post(url)
    assert response.status_code == 302
    announcement.refresh_from_db()
    assert announcement.published == (action == "unpublish")

    user.admin = True
    user.save()
    response = client.post(url)
    assert response.status_code == 200
    announcement.refresh_from_db()
    assert announcement.published == (action == "publish")
