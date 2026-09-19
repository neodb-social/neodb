from io import StringIO

import pytest
from django.core.management import call_command

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
    def test_orphan_mirror_merges_into_the_handle_holder(self):
        """
        Merging an alias away on the Takahe side leaves its mirror row behind,
        still claiming a handle another identity now holds. get_remote() takes
        the first match, so the stale row can answer for the wrong identity.
        """
        domain = Domain.get_remote_domain("example.com")
        holder = make_identity(
            101, "https://example.com/ruben", username="ruben", domain=domain
        )
        orphan = make_mirror(102, "ruben", "example.com")

        output = run(fix=True, yes=True)

        assert "orphan" in output
        orphan.refresh_from_db()
        assert orphan.deleted is not None
        assert APIdentity.objects.filter(pk=holder.pk, deleted__isnull=True).exists()

    def test_scan_only_by_default(self):
        domain = Domain.get_remote_domain("example.com")
        make_identity(103, "https://example.com/ruben", username="ruben", domain=domain)
        orphan = make_mirror(104, "ruben", "example.com")

        output = run()

        assert "1 repairable" in output
        orphan.refresh_from_db()
        assert orphan.deleted is None
        assert not APIdentity.objects.filter(pk=103).exists()

    def test_mirror_without_a_holder_is_left_alone(self):
        orphan = make_mirror(105, "nobody", "example.com")

        output = run(fix=True, yes=True)

        assert "no identity holds that handle" in output
        orphan.refresh_from_db()
        assert orphan.deleted is None

    def test_consistent_mirror_is_not_reported(self):
        domain = Domain.get_remote_domain("example.com")
        make_identity(106, "https://example.com/ok", username="ok", domain=domain)
        make_mirror(106, "ok", "example.com")

        output = run()

        assert "0 mismatched" in output
