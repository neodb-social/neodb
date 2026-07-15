import threading

import pytest

from mastodon.models import EmailAccount, MastodonAccount
from users.jobs import MastodonUserSync
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestMastodonUserSync:
    @pytest.fixture(autouse=True)
    def setup_data(self, monkeypatch):
        # interval_hours=24 makes a single batch so every user id matches
        # regardless of the wall-clock hour the test runs at
        monkeypatch.setattr(MastodonUserSync, "interval_hours", 24)
        self.synced: list[int] = []
        self.synced_kwargs: list[dict] = []
        self.lock = threading.Lock()

        def fake_sync(user_id: int, sleep_hours: int = 0, inactive_days=None):
            with self.lock:
                self.synced.append(user_id)
                self.synced_kwargs.append(
                    {"sleep_hours": sleep_hours, "inactive_days": inactive_days}
                )

        monkeypatch.setattr(User, "sync_accounts_task", fake_sync)

    def _make_mastodon_user(self, username: str) -> User:
        user = User.register(username=username)
        MastodonAccount.objects.create(
            handle=f"{username}@social.example",
            user=user,
            domain="social.example",
            uid=username,
        )
        return user

    def test_syncs_eligible_users_inline(self):
        u1 = self._make_mastodon_user("alice")
        u2 = self._make_mastodon_user("bob")
        MastodonUserSync().run()
        assert sorted(self.synced) == sorted([u1.pk, u2.pk])
        assert all(
            kw == {"sleep_hours": 24, "inactive_days": 30} for kw in self.synced_kwargs
        )

    def test_skips_email_only_users(self):
        user = User.register(username="mailonly")
        EmailAccount.objects.create(
            handle="mailonly@example.com",
            user=user,
            domain="example.com",
            uid="mailonly@example.com",
        )
        MastodonUserSync().run()
        assert self.synced == []

    def test_skips_inactive_users(self):
        user = self._make_mastodon_user("gone")
        user.is_active = False
        user.save(update_fields=["is_active"])
        MastodonUserSync().run()
        assert self.synced == []

    def test_one_failure_does_not_stop_batch(self, monkeypatch):
        u1 = self._make_mastodon_user("crash")
        u2 = self._make_mastodon_user("fine")
        synced = self.synced
        lock = self.lock

        def flaky_sync(user_id: int, sleep_hours: int = 0, inactive_days=None):
            if user_id == u1.pk:
                raise RuntimeError("boom")
            with lock:
                synced.append(user_id)

        monkeypatch.setattr(User, "sync_accounts_task", flaky_sync)
        MastodonUserSync().run()
        assert synced == [u2.pk]
