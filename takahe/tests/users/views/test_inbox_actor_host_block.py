import json

import pytest

from users.models import Domain, Identity, InboxMessage


def _post(client, identity, actor_uri: str):
    return client.post(
        identity.inbox_uri,
        data=json.dumps(
            {
                "@context": "https://www.w3.org/ns/activitystreams",
                "id": f"{actor_uri}#create",
                "type": "Create",
                "actor": actor_uri,
                "object": {"id": f"{actor_uri}/1", "type": "Note", "content": "x"},
            }
        ),
        content_type="application/activity+json",
    )


@pytest.mark.django_db
def test_inbox_blocks_actor_host_behind_foreign_handle(client, identity):
    """
    An actor stored under a handle on another domain is still discarded when
    the host serving it is blocked.
    """
    Domain.objects.create(domain="blocked.test", local=False, blocked=True)
    Identity.objects.create(
        actor_uri="https://sub.blocked.test/users/eve",
        local=False,
        username="eve",
        domain=Domain.get_remote_domain("innocent.test"),
    )

    response = _post(client, identity, "https://sub.blocked.test/users/eve")

    assert response.status_code == 202
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_inbox_unknown_actor_on_unblocked_host_reaches_signature_check(
    client, identity
):
    response = _post(client, identity, "https://fine.test/users/new")
    assert response.status_code == 401
    assert not Domain.objects.filter(domain="fine.test").exists()


@pytest.mark.django_db
def test_inbox_actor_on_local_domain_does_not_crash(client, identity):
    """The block check used to create a remote Domain row for our own domain."""
    response = _post(client, identity, f"https://{identity.domain_id}/users/ghost")
    assert response.status_code == 401


@pytest.mark.django_db
def test_inbox_blocks_foreign_handle_domain(client, identity):
    Domain.objects.create(domain="blocked-handle.test", local=False, blocked=True)
    Identity.objects.create(
        actor_uri="https://host.test/users/eve",
        local=False,
        username="eve",
        domain=Domain.get_remote_domain("blocked-handle.test"),
    )

    response = _post(client, identity, "https://host.test/users/eve")

    assert response.status_code == 202
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_inbox_checks_shared_actor_and_handle_domain_once(
    client, identity, monkeypatch
):
    Identity.objects.create(
        actor_uri="https://same.test/users/bob",
        local=False,
        username="bob",
        domain=Domain.get_remote_domain("same.test"),
    )
    checked: list[str] = []
    real_recursively_blocked = Domain.recursively_blocked

    def spy(self: Domain) -> bool:
        checked.append(self.domain)
        return real_recursively_blocked(self)

    monkeypatch.setattr(Domain, "recursively_blocked", spy)

    response = _post(client, identity, "https://same.test/users/bob")

    assert response.status_code == 401
    assert checked == ["same.test"]
