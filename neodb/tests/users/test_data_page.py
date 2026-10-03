import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from common.models import SiteConfig
from journal.exporters import NdjsonExporter
from journal.importers import (
    GoodreadsImporter,
    LetterboxdImporter,
    RymImporter,
    SteamImporter,
    TraktImporter,
)
from users.models import Task, User
from users.views.data import IMPORT_SOURCES, RECENT_ACTIVITY_LIMIT

pytestmark = pytest.mark.django_db(databases="__all__")

RUNNING_WARNING = "An import is still running"


@pytest.fixture
def user() -> User:
    return User.register(email="datapage@example.com", username="datapage")


@pytest.fixture
def client(user: User) -> Client:
    c = Client()
    c.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return c


@pytest.fixture
def all_sources(monkeypatch):
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
    monkeypatch.setattr(SiteConfig.system, "enable_import_twitter", True)
    monkeypatch.setattr(SiteConfig.system, "enable_import_mastodon", True)


def _task(cls: type[Task], user: User, state: int, minutes_ago: int = 0) -> Task:
    task = cls.create(user)
    stamp = timezone.now() - datetime.timedelta(minutes=minutes_ago)
    cls.objects.filter(pk=task.pk).update(
        state=state, created_time=stamp, edited_time=stamp
    )
    task.refresh_from_db()
    return task


def test_hub_puts_export_after_import(client, all_sources):
    content = client.get(reverse("users:data")).content.decode()
    assert content.index('id="import"') < content.index('id="import-posts"')
    assert content.index('id="import-posts"') < content.index('id="export"')
    for source in IMPORT_SOURCES:
        assert f'href="{reverse(source.url_name)}"' in content
    assert f'href="{reverse("users:info")}#social-graph"' in content


def test_hub_hides_disabled_sources(client, monkeypatch):
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
    monkeypatch.setattr(SiteConfig.system, "enable_import_twitter", False)
    monkeypatch.setattr(SiteConfig.system, "enable_import_mastodon", False)
    content = client.get(reverse("users:data")).content.decode()
    assert reverse("users:import_twitter") not in content
    assert reverse("users:import_mastodon") not in content
    assert client.get(reverse("users:import_twitter")).url == reverse("users:data")


def test_hub_shows_annual_summary_in_sidebar(client):
    content = client.get(reverse("users:data")).content.decode()
    year = timezone.now().year
    assert reverse("journal:wrapped", args=[year]) in content


@pytest.mark.parametrize("source", IMPORT_SOURCES, ids=lambda s: s.key)
def test_source_page_renders(client, all_sources, source):
    response = client.get(reverse(source.url_name))
    assert response.status_code == 200
    content = response.content.decode()
    assert str(source.heading) in content
    assert f'href="{reverse("users:data")}"' in content
    assert 'class="import-rules"' in content
    assert RUNNING_WARNING not in content


def test_recent_activity_lists_newest_tasks_first(client, user):
    _task(GoodreadsImporter, user, Task.States.complete, minutes_ago=30)
    _task(NdjsonExporter, user, Task.States.complete, minutes_ago=10)
    _task(LetterboxdImporter, user, Task.States.started, minutes_ago=1)
    content = client.get(reverse("users:data")).content.decode()
    activity = content[content.index('id="activity"') : content.index('id="import"')]
    assert (
        activity.index(reverse("users:import_letterboxd"))
        < activity.index(f"{reverse('users:data')}#export")
        < activity.index(reverse("users:import_goodreads"))
    )
    # the running task polls in compact form, without its failed-item lists
    assert "?compact=1" in activity
    assert reverse("users:user_task_download", args=["journal.ndjsonexporter"]) in (
        activity
    )


def test_recent_activity_is_limited(client, user):
    for i, cls in enumerate(
        [
            GoodreadsImporter,
            LetterboxdImporter,
            TraktImporter,
            SteamImporter,
            RymImporter,
            NdjsonExporter,
        ]
    ):
        _task(cls, user, Task.States.complete, minutes_ago=i)
    content = client.get(reverse("users:data")).content.decode()
    activity = content[content.index('id="activity"') : content.index('id="import"')]
    assert activity.count("<li>") == RECENT_ACTIVITY_LIMIT


def test_hub_without_tasks_has_no_activity(client):
    content = client.get(reverse("users:data")).content.decode()
    assert 'id="activity"' not in content


def test_running_import_warns_on_other_source_pages(client, user):
    _task(GoodreadsImporter, user, Task.States.started)
    content = client.get(reverse("users:import_letterboxd")).content.decode()
    assert RUNNING_WARNING in content
    assert 'hx-confirm="' in content
    assert 'hx-disinherit="hx-confirm"' in content


def test_finished_or_stale_import_does_not_warn(client, user):
    _task(GoodreadsImporter, user, Task.States.complete)
    _task(TraktImporter, user, Task.States.pending, minutes_ago=120)
    content = client.get(reverse("users:import_letterboxd")).content.decode()
    assert RUNNING_WARNING not in content
    assert "hx-confirm" not in content


def test_compact_status_does_not_swap_the_rym_section(client, user):
    task = RymImporter.create(user, phase="preview")
    RymImporter.objects.filter(pk=task.pk).update(state=Task.States.complete)
    url = reverse("users:user_task_status", args=["journal.rymimporter"])
    assert client.get(url).headers.get("HX-Retarget") == "#rym"
    response = client.get(url + "?compact=1")
    assert "HX-Retarget" not in response.headers
    assert 'class="task-status"' in response.content.decode()


def test_steam_import_returns_to_its_page(client, user, monkeypatch):
    monkeypatch.setattr(SteamImporter, "enqueue", lambda self: None)
    response = client.post(
        reverse("users:import_steam"), {"steam_id": "1", "source[]": ["wishlist"]}
    )
    assert response.url == reverse("users:import_steam")
    assert SteamImporter.latest_task(user) is not None


def test_older_running_import_behind_a_finished_one_still_warns(client, user):
    _task(GoodreadsImporter, user, Task.States.started, minutes_ago=5)
    _task(GoodreadsImporter, user, Task.States.complete, minutes_ago=1)
    content = client.get(reverse("users:import_trakt")).content.decode()
    assert RUNNING_WARNING in content


def test_partial_save_refreshes_the_heartbeat(user):
    task = _task(GoodreadsImporter, user, Task.States.started, minutes_ago=120)
    before = task.edited_time
    task.metadata["processed"] = 1
    task.save(update_fields=["metadata"])
    task.refresh_from_db()
    assert task.edited_time > before


def test_steam_settings_page_confirms_while_an_import_runs(client, user):
    session = client.session
    session["steam_id"] = "1"
    session.save()
    url = reverse("users:steam_import_page")
    assert RUNNING_WARNING not in client.get(url).content.decode()
    _task(GoodreadsImporter, user, Task.States.started)
    content = client.get(url).content.decode()
    assert RUNNING_WARNING in content
    assert 'onsubmit="return confirm(' in content
