import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from catalog.models import Edition, Podcast, PodcastEpisode
from common.models import SiteConfig
from journal.models import Mark, ShelfType
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.fixture
def member() -> Client:
    user = User.register(email="feedpage@example.com", username="feedpage")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return client


def test_feed_header_and_tabs(member, monkeypatch):
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
    monkeypatch.setattr(SiteConfig.system, "feed_show_local", True)
    monkeypatch.setattr(SiteConfig.system, "feed_show_world", False)

    content = member.get(reverse("social:feed")).content.decode()

    assert 'class="dc-mh"' in content
    assert reverse("social:notification") in content
    # the active tab is a pill without a link; the others link to their feed
    assert '<a class="on" aria-current="page">Following</a>' in content
    assert f'href="{reverse("social:focus")}"' in content
    assert f'href="{reverse("social:local")}"' in content
    assert reverse("social:world") not in content


def test_focus_feed_marks_its_tab(member):
    content = member.get(reverse("social:focus")).content.decode()
    assert '<a class="on" aria-current="page">Marks and reviews</a>' in content
    assert f'href="{reverse("social:feed")}"' in content


def test_empty_feed_offers_next_steps(member):
    content = member.get(reverse("social:data")).content.decode()
    assert 'class="dc-empty"' in content
    assert reverse("catalog:discover") in content
    assert reverse("users:data") in content


@pytest.mark.parametrize("page", ["social:feed", "social:notification"])
def test_in_progress_sidebar(page):
    user = User.register(email="inprogress@example.com", username="inprogress")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    show = Podcast.objects.create(title="Nightly Show")
    episode = PodcastEpisode.objects.create(
        title="Episode one",
        program=show,
        pub_date=timezone.now(),
        media_url="https://example.org/one.mp3",
    )
    book = Edition.objects.create(title="Half Read Book")
    Mark(user.identity, show).update(ShelfType.PROGRESS)
    Mark(user.identity, book).update(ShelfType.PROGRESS)

    content = client.get(reverse(page)).content.decode()

    assert "Recent podcast episodes" in content
    assert f'data-media="{episode.media_url}"' in content
    # the markup alone is inert; podcast.js binds the click that plays it
    assert "js/podcast.js" in content
    assert "Currently reading" in content
    assert book.display_title in content
    assert 'class="dc-cards dc-sidebar-cards"' in content
