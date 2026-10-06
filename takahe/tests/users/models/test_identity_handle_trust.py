import httpx
import pytest
from pytest_httpx import HTTPXMock

from core.files import SSRFAttemptError, make_safe_client
from core.files import check_url_safety as real_check_url_safety
from users.models import Domain, Identity
from users.services import IdentityService


def _webfinger(httpx_mock: HTTPXMock, handle: str, subject: str, actor: str) -> None:
    domain = handle.split("@")[1]
    httpx_mock.add_response(
        url=f"https://{domain}/.well-known/webfinger?resource=acct:{handle}",
        headers={"Content-Type": "application/json"},
        json={
            "subject": f"acct:{subject}",
            "links": [
                {"rel": "self", "type": "application/activity+json", "href": actor}
            ],
        },
    )


def _actor(httpx_mock: HTTPXMock, actor_uri: str, username: str, **extra) -> None:
    httpx_mock.add_response(
        url=actor_uri,
        headers={"Content-Type": "application/activity+json"},
        json={
            "@context": [
                "https://www.w3.org/ns/activitystreams",
                "https://w3id.org/security/v1",
            ],
            "id": actor_uri,
            "type": "Person",
            "inbox": f"{actor_uri}inbox/",
            "preferredUsername": username,
            **extra,
        },
    )


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_fetch_actor_refuses_handle_claimed_on_another_domain(
    httpx_mock, config_system
):
    """
    A server cannot give its actor a handle on a domain whose own WebFinger
    names someone else.
    """
    actor_uri = "https://evil.test/users/eve/"
    identity = Identity.objects.create(actor_uri=actor_uri, local=False)
    _actor(httpx_mock, actor_uri, "eve")
    _webfinger(httpx_mock, "eve@evil.test", "someone@victim.test", actor_uri)
    _webfinger(
        httpx_mock,
        "someone@victim.test",
        "someone@victim.test",
        "https://victim.test/users/someone",
    )

    assert identity.fetch_actor()

    identity = Identity.objects.get(pk=identity.pk)
    assert (identity.username, identity.domain_id) == ("eve", "evil.test")


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_fetch_actor_adopts_confirmed_split_domain_handle(httpx_mock, config_system):
    actor_uri = "https://social.split.test/users/alice/"
    identity = Identity.objects.create(actor_uri=actor_uri, local=False)
    _actor(httpx_mock, actor_uri, "alice")
    _webfinger(httpx_mock, "alice@social.split.test", "alice@split.test", actor_uri)
    _webfinger(httpx_mock, "alice@split.test", "alice@split.test", actor_uri)

    assert identity.fetch_actor()

    identity = Identity.objects.get(pk=identity.pk)
    assert (identity.username, identity.domain_id) == ("alice", "split.test")


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_fetch_actor_keeps_stored_handle_when_claim_unverifiable(
    httpx_mock, config_system
):
    """
    A claimed domain that cannot be reached right now leaves an already
    stored handle alone rather than flipping it to the host handle.
    """
    actor_uri = "https://social.split.test/users/alice/"
    identity = Identity.objects.create(
        actor_uri=actor_uri,
        local=False,
        username="alice",
        domain=Domain.get_remote_domain("split.test"),
    )
    _actor(httpx_mock, actor_uri, "alice")
    _webfinger(httpx_mock, "alice@social.split.test", "alice@split.test", actor_uri)
    httpx_mock.add_response(
        url="https://split.test/.well-known/webfinger?resource=acct:alice@split.test",
        status_code=404,
    )

    assert identity.fetch_actor()

    identity = Identity.objects.get(pk=identity.pk)
    assert (identity.username, identity.domain_id) == ("alice", "split.test")


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_fetch_actor_refuses_handle_on_local_domain(httpx_mock, config_system, domain):
    """
    A subject on one of our own domains is refused without asking ourselves,
    and no longer fails the save with an IntegrityError.
    """
    actor_uri = "https://evil.test/users/eve/"
    identity = Identity.objects.create(actor_uri=actor_uri, local=False)
    _actor(httpx_mock, actor_uri, "eve")
    _webfinger(httpx_mock, "eve@evil.test", f"admin@{domain.domain}", actor_uri)

    assert identity.fetch_actor()

    identity = Identity.objects.get(pk=identity.pk)
    assert (identity.username, identity.domain_id) == ("eve", "evil.test")
    assert not any(r.url.host == domain.domain for r in httpx_mock.get_requests()), (
        "our own WebFinger should not be queried"
    )


@pytest.mark.django_db
def test_fetch_actor_skips_actor_on_local_domain(httpx_mock, config_system, domain):
    """A remote actor URI on our own domain used to fail with IntegrityError."""
    actor_uri = f"https://{domain.domain}/users/ghost/"
    _actor(httpx_mock, actor_uri, "ghost")
    identity = Identity(actor_uri=actor_uri, local=False)
    assert identity.fetch_actor() is False
    assert not Identity.objects.filter(actor_uri=actor_uri).exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_by_handle_fetch_refuses_foreign_subject(httpx_mock, config_system):
    actor_uri = "https://evil.test/users/eve/"
    _webfinger(httpx_mock, "eve@evil.test", "someone@victim.test", actor_uri)
    httpx_mock.add_response(
        url="https://victim.test/.well-known/webfinger?resource=acct:someone@victim.test",
        status_code=404,
    )

    found = Identity.by_handle("eve@evil.test", fetch=True)

    assert found is not None
    identity = Identity.objects.get(pk=found.pk)
    assert identity.actor_uri == actor_uri
    assert (identity.username, identity.domain_id) == ("eve", "evil.test")
    assert not Identity.objects.filter(username="someone").exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_by_handle_fetch_adopts_confirmed_subject(httpx_mock, config_system):
    actor_uri = "https://social.split.test/users/alice/"
    _webfinger(httpx_mock, "alice@social.split.test", "alice@split.test", actor_uri)
    _webfinger(httpx_mock, "alice@split.test", "alice@split.test", actor_uri)

    found = Identity.by_handle("alice@social.split.test", fetch=True)

    assert found is not None
    identity = Identity.objects.get(pk=found.pk)
    assert (identity.username, identity.domain_id) == ("alice", "split.test")


@pytest.mark.django_db
def test_fetch_collection_blocks_non_public_url(monkeypatch, httpx_mock):
    monkeypatch.setattr("core.files.check_url_safety", real_check_url_safety)
    with make_safe_client() as client:
        assert Identity.fetch_collection(
            client, "http://169.254.169.254/latest/meta-data/"
        ) == (0, [])
    assert httpx_mock.get_requests() == []


@pytest.mark.django_db
def test_sync_actor_does_not_fetch_non_public_collections(
    monkeypatch, httpx_mock, remote_identity
):
    """The collection URIs of an actor document go through the SSRF hook."""
    monkeypatch.setattr("core.files.check_url_safety", real_check_url_safety)
    remote_identity.featured_collection_uri = None
    remote_identity.featured_tags_uri = None
    remote_identity.followers_uri = "http://10.0.0.1/followers"
    remote_identity.following_uri = "http://127.0.0.1/following"
    remote_identity.outbox_uri = "http://169.254.169.254/outbox"
    remote_identity.save()

    IdentityService.handle_internal_sync_actor({"identity": remote_identity.pk})

    assert httpx_mock.get_requests() == []
    remote_identity.refresh_from_db()
    assert remote_identity.stats["followers_count"] == 0


@pytest.mark.django_db
def test_fetch_nodeinfo_does_not_follow_non_public_href(monkeypatch, httpx_mock):
    def check(request: httpx.Request) -> None:
        if request.url.host == "169.254.169.254":
            raise SSRFAttemptError("blocked")

    monkeypatch.setattr("core.files.check_url_safety", check)
    httpx_mock.add_response(
        url="https://nodeinfo.test/.well-known/nodeinfo",
        json={
            "links": [
                {
                    "rel": "http://nodeinfo.diaspora.software/ns/schema/2.0",
                    "href": "http://169.254.169.254/latest/meta-data/",
                }
            ]
        },
    )
    domain = Domain.objects.create(domain="nodeinfo.test", local=False)

    assert domain.fetch_nodeinfo() is None
    assert [r.url.host for r in httpx_mock.get_requests()] == ["nodeinfo.test"]
