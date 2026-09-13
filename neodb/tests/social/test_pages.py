import pytest
import requests
from django.test import Client

from users.models import User


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_social_pages_logged_in_user(live_server):
    user = User.register(email="timeline@example.com", username="timelineuser")
    authed_client = Client()
    authed_client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    auth_cookies = {key: morsel.value for key, morsel in authed_client.cookies.items()}

    response = requests.get(
        f"{live_server.url}/timeline/", cookies=auth_cookies, timeout=5
    )
    assert response.status_code == 200

    response = requests.get(
        f"{live_server.url}/timeline/focus", cookies=auth_cookies, timeout=5
    )
    assert response.status_code == 200

    response = requests.get(
        f"{live_server.url}/timeline/data", cookies=auth_cookies, timeout=5
    )
    assert response.status_code == 200

    response = requests.get(
        f"{live_server.url}/timeline/notification", cookies=auth_cookies, timeout=5
    )
    assert response.status_code == 200

    response = requests.get(
        f"{live_server.url}/timeline/events", cookies=auth_cookies, timeout=5
    )
    assert response.status_code == 200

    response = requests.get(
        f"{live_server.url}/timeline/unread_notifications_status",
        cookies=auth_cookies,
        timeout=5,
    )
    assert response.status_code == 200

    response = requests.get(
        f"{live_server.url}/timeline/search_data?q=hello&lastpage=0",
        cookies=auth_cookies,
        timeout=5,
    )
    assert response.status_code == 200

    response = authed_client.post("/timeline/dismiss_notification", follow=True)
    assert response.status_code == 200
