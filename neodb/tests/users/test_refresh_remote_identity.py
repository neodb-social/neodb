from unittest import mock

import pytest

from takahe.models import Domain, Identity
from takahe.utils import Takahe


@pytest.fixture
def remote_identity() -> Identity:
    domain = Domain.objects.create(domain="refresh.example", local=False)
    return Identity.objects.create(
        actor_uri="https://refresh.example/users/someone",
        username="someone",
        domain=domain,
        local=False,
        aliases=[],
    )


@pytest.mark.django_db(databases="__all__")
def test_private_actor_uri_is_not_fetched(remote_identity: Identity) -> None:
    with (
        mock.patch("takahe.utils.is_valid_url", return_value=False),
        mock.patch("httpx.get") as get,
    ):
        Takahe.refresh_remote_identity(remote_identity.pk)
    get.assert_not_called()


@pytest.mark.django_db(databases="__all__")
def test_refresh_does_not_follow_redirects(remote_identity: Identity) -> None:
    with (
        mock.patch("takahe.utils.is_valid_url", return_value=True),
        mock.patch("httpx.get") as get,
    ):
        get.return_value.status_code = 200
        get.return_value.json.return_value = {"alsoKnownAs": ["https://a.example/u"]}
        Takahe.refresh_remote_identity(remote_identity.pk)
    assert get.call_args.kwargs["follow_redirects"] is False
    remote_identity.refresh_from_db()
    assert remote_identity.aliases == ["https://a.example/u"]
