from unittest import mock

import pytest

from mastodon.models import MastodonAccount, MastodonApplication
from mastodon.models.mastodon import Mastodon
from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _setup(server_version: str) -> MastodonAccount:
    MastodonApplication.objects.create(
        domain_name="m.test",
        api_domain="m.test",
        server_version=server_version,
        app_id="1",
        client_id="cid",
        client_secret="csecret",
        vapid_key="",
    )
    victim = User.register(email="victim@example.com", username="victim")
    return MastodonAccount.objects.create(
        user=victim,
        domain="m.test",
        uid="old-uid",
        handle="alice@m.test",
        account_data={"id": "old-uid", "username": "alice"},
    )


def _authenticate() -> MastodonAccount | None:
    verified = (200, {"id": "new-uid", "username": "alice"})
    with (
        mock.patch.object(MastodonAccount, "verify_account", return_value=verified),
        mock.patch.object(Takahe, "fetch_remote_identity"),
    ):
        return Mastodon.authenticate("m.test", "token", "")


def test_reused_username_does_not_inherit_the_old_account() -> None:
    existing = _setup("Mastodon 4.3.0")

    account = _authenticate()

    assert account is not None
    assert account.pk is None
    existing.refresh_from_db()
    assert existing.uid == "old-uid"


def test_firefish_id_change_keeps_the_account() -> None:
    existing = _setup("2024.3.1 (compatible; Sharkey 2024.3.1)")

    account = _authenticate()

    assert account is not None
    assert account.pk == existing.pk
    assert account.uid == "new-uid"
