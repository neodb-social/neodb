from io import StringIO

import pytest
from django.core.management import call_command

from journal.models import Tag
from takahe.models import Domain, Identity
from users.models.apidentity import APIdentity


def run(**kwargs) -> str:
    out = StringIO()
    call_command("fixapidentitymirror", stdout=out, **kwargs)
    return out.getvalue()


def make_identity(pk: int, actor_uri: str, username=None, domain=None) -> Identity:
    return Identity.objects.create(
        pk=pk,
        actor_uri=actor_uri,
        username=username,
        domain=domain,
        local=False,
        private_key="",
        public_key="",
    )


def make_mirror(pk: int, username: str, domain_name: str) -> APIdentity:
    return APIdentity.objects.create(
        pk=pk,
        user=None,
        local=False,
        username=username,
        domain_name=domain_name,
        anonymous_viewable=False,
    )


@pytest.mark.django_db(databases="__all__")
class TestFixAPIdentityMirror:
    def test_orphan_owning_nothing_is_retired(self):
        """
        Merging an alias away on the Takahe side leaves its mirror row behind,
        still claiming a handle another identity now holds. get_remote() takes
        the first match, so the stale row can answer for the wrong identity.
        """
        domain = Domain.get_remote_domain("example.com")
        make_identity(101, "https://example.com/ruben", username="ruben", domain=domain)
        orphan = make_mirror(102, "ruben", "example.com")

        output = run(fix=True, yes=True)

        assert "orphan" in output
        orphan.refresh_from_db()
        assert orphan.deleted is not None

    def test_stale_mirror_is_resynced_from_its_own_identity(self):
        """
        A handle is no evidence of who should own the data: the identity is
        the one behind this very row, found by primary key, never whoever
        happens to hold the handle now.
        """
        domain = Domain.get_remote_domain("example.com")
        make_identity(
            103, "https://example.com/renamed", username="renamed", domain=domain
        )
        # Somebody else took the name this mirror still claims
        make_identity(
            104, "https://example.com/oldname", username="oldname", domain=domain
        )
        make_mirror(104, "oldname", "example.com")
        mirror = make_mirror(103, "oldname", "example.com")

        run(fix=True, yes=True)

        mirror.refresh_from_db()
        assert mirror.username == "renamed"
        assert mirror.deleted is None

    def test_stale_mirror_is_resynced_when_nobody_took_the_old_handle(self):
        """
        Nothing needs to hold the old handle for the row to be wrong: the
        identity behind it renamed, and the mirror still says the old name.
        Raised on PR 1917 by Sentry's reviewer.
        """
        domain = Domain.get_remote_domain("example.com")
        make_identity(
            109, "https://example.com/renamed", username="renamed", domain=domain
        )
        mirror = make_mirror(109, "oldname", "example.com")

        output = run(fix=True, yes=True)

        assert "stale" in output
        mirror.refresh_from_db()
        assert mirror.username == "renamed"
        assert mirror.domain_name == "example.com"
        assert mirror.deleted is None

    def test_orphan_that_owns_data_is_only_reported(self):
        """
        Retiring a row that owns journal data would hide it, and no handle is
        good enough reason to give that data to another identity.
        """
        orphan = make_mirror(105, "someone", "example.com")
        tag = Tag.objects.create(owner=orphan, title="kept")

        output = run(fix=True, yes=True)

        assert "orphan-owns-data" in output
        orphan.refresh_from_db()
        assert orphan.deleted is None
        tag.refresh_from_db()
        assert tag.owner_id == orphan.pk

    def test_scan_only_by_default(self):
        domain = Domain.get_remote_domain("example.com")
        make_identity(106, "https://example.com/ruben", username="ruben", domain=domain)
        orphan = make_mirror(107, "ruben", "example.com")

        output = run()

        assert "1 repairable" in output
        orphan.refresh_from_db()
        assert orphan.deleted is None

    def test_consistent_mirror_is_not_reported(self):
        domain = Domain.get_remote_domain("example.com")
        make_identity(108, "https://example.com/ok", username="ok", domain=domain)
        make_mirror(108, "ok", "example.com")

        output = run()

        assert "0 mismatched" in output
