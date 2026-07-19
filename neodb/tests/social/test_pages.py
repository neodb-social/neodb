import pytest
import requests
from django.test import Client
from django.urls import reverse

from catalog.models import Edition
from journal.models import Mark, Note, ShelfType
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


@pytest.mark.django_db(databases="__all__")
def test_currently_reading_sidebar_progress_badges():
    user = User.register(email="sidebar-progress@example.com", username="sidebarprog")
    progressed_book = Edition.objects.create(title="Sidebar Progress Book")
    unstarted_book = Edition.objects.create(title="Sidebar Unstarted Book")
    progressed_mark = Mark(user.identity, progressed_book)
    progressed_mark.update(ShelfType.PROGRESS, visibility=0)
    progressed_mark.set_progress(Note.ProgressType.PAGE, "22")
    Mark(user.identity, unstarted_book).update(ShelfType.PROGRESS, visibility=0)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    response = client.get("/timeline/")

    assert response.status_code == 200
    content = response.content.decode()
    progressed_url = reverse("journal:note", args=[progressed_book.uuid])
    unstarted_url = reverse("journal:note", args=[unstarted_book.uuid])
    assert f'hx-get="{progressed_url}?mode=progress"' in content
    assert f'hx-get="{unstarted_url}?mode=progress"' in content
    assert content.count('class="card progress-card"') >= 2
    assert content.count('class="progress-badge"') >= 2
    assert "p22" in content
    assert 'title="Page 22"' in content
    assert "fa-solid fa-percent" in content
