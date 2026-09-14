import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from takahe.models import Domain
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _add_neodb_peer(domain: str, name: str):
    Domain.objects.create(
        domain=domain,
        local=False,
        state="updated",
        nodeinfo={
            "protocols": ["neodb"],
            "metadata": {"nodeName": name, "nodeEnvironment": "production"},
        },
    )


def test_about_page_for_anonymous_visitor():
    admin = User.register(email="boss@example.com", username="boss")
    admin.is_superuser = True
    admin.save(update_fields=["is_superuser"])
    cache.set("catalog_stats", [{"label": "Book", "count": 12}])
    cache.set("instance_info_stats", {"user_count": 3, "status_count": 7})
    _add_neodb_peer("peer1.example.com", "Peer One")
    _add_neodb_peer("peer2.example.com", "Peer Two")
    # the peer list is cached for 30 minutes, so another test may have filled it
    cache.delete("neodb_peers_active")

    content = Client().get(reverse("common:about")).content.decode()

    assert 'id="about"' in content
    assert reverse("users:login") in content
    assert 'id="instance"' in content
    assert 'id="team"' in content
    assert "boss" in content
    assert ">12<" in content
    assert ">7<" in content
    assert 'id="peers"' in content
    assert "Peer One" in content
    assert "<b>2</b>" in content


def test_about_page_for_member_skips_sign_in():
    user = User.register(email="aboutme@example.com", username="aboutme")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    content = client.get(reverse("common:about")).content.decode()

    assert 'id="about"' in content
    assert reverse("users:login") not in content
    assert reverse("catalog:discover") in content
