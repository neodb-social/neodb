import pytest
from django.test import Client
from django.urls import reverse

from common.models import SiteConfig
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
