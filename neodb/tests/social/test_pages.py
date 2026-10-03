import pytest
from django.test import Client

from users.models import User


@pytest.mark.django_db(databases="__all__")
def test_social_pages_logged_in_user(client: Client) -> None:
    user = User.register(email="timeline@example.com", username="timelineuser")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    for path in [
        "/timeline/",
        "/timeline/focus",
        "/timeline/data",
        "/timeline/notification",
        "/timeline/events",
        "/timeline/unread_notifications_status",
        "/timeline/search_data?q=hello&lastpage=0",
    ]:
        assert client.get(path, follow=True).status_code == 200, path

    response = client.post("/timeline/dismiss_notification", follow=True)
    assert response.status_code == 200
