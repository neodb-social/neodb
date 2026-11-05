import builtins
from datetime import timedelta

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.utils import timezone

from common.models import JobManager
from users.jobs import cleanup
from users.jobs.cleanup import SessionCleanup, prune_expired_sessions


def _session(expire_in: timedelta) -> str:
    store = SessionStore()
    store["next_url"] = "/"
    store.create()
    Session.objects.filter(session_key=store.session_key).update(
        expire_date=timezone.now() + expire_in
    )
    assert store.session_key
    return store.session_key


@pytest.mark.django_db(databases="__all__")
class TestSessionCleanup:
    def test_deletes_only_expired_sessions(self) -> None:
        expired = [_session(timedelta(hours=-1)) for _ in range(5)]
        live = _session(timedelta(days=1))
        assert prune_expired_sessions(batch_size=2) == len(expired)
        assert list(Session.objects.values_list("pk", flat=True)) == [live]

    def test_session_renewed_after_select_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        renewed = _session(timedelta(hours=-1))

        def select_then_renew(iterable):
            keys = builtins.list(iterable)
            Session.objects.filter(pk=renewed).update(
                expire_date=timezone.now() + timedelta(days=1)
            )
            return keys

        monkeypatch.setattr(cleanup, "list", select_then_renew, raising=False)
        assert prune_expired_sessions() == 0
        assert Session.objects.filter(pk=renewed).exists()

    def test_nothing_to_delete(self) -> None:
        _session(timedelta(days=1))
        assert prune_expired_sessions() == 0
        assert Session.objects.count() == 1

    def test_job_registered_daily(self) -> None:
        assert SessionCleanup in JobManager.registry
        assert SessionCleanup.get_interval() == timedelta(days=1)

    def test_job_run(self) -> None:
        _session(timedelta(seconds=-1))
        SessionCleanup().run()
        assert not Session.objects.exists()
