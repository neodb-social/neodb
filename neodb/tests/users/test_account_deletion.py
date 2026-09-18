import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from catalog.models import Edition
from journal.exporters import NdjsonExporter
from journal.models import CrosspostRetry, Mark, ShelfMember, ShelfType
from mastodon.models import EmailAccount
from takahe.ap_handlers import identity_deleted
from takahe.models import Identity
from users.jobs.cleanup import reconcile_deleted_users
from users.models import Preference, Task, User, Webhook


@pytest.mark.django_db(databases="__all__")
class TestUserClear:
    @pytest.fixture(autouse=True)
    def setup_data(self, tmp_path):
        self.user = User.register(email="del@test.com", username="del_user")
        EmailAccount.objects.create(
            handle="del@test.com", uid="del", domain="test.com", user=self.user
        )
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Deleted Book")
        Mark(self.identity, self.book).update(ShelfType.WISHLIST, "c", 5, [], 0)
        self.export = tmp_path / "export.zip"
        self.export.write_text("journal")
        self.task = NdjsonExporter.objects.create(
            user=self.user, metadata={"file": str(self.export)}
        )
        Webhook.objects.create(
            user=self.user, application_id=1, url="https://example.org/hook"
        )
        piece = ShelfMember.objects.filter(owner=self.identity).first()
        assert piece
        CrosspostRetry.objects.create(user=self.user, piece=piece, platform="mastodon")

    def test_clear_removes_user_owned_records_and_files(self):
        assert Preference.objects.filter(user=self.user).exists()
        self.user.clear()
        assert not self.user.is_active
        assert self.user.last_name == self.user.username
        assert not Task.objects.filter(user=self.user).exists()
        assert not Webhook.objects.filter(user=self.user).exists()
        assert not CrosspostRetry.objects.filter(user=self.user).exists()
        assert not Preference.objects.filter(user=self.user).exists()
        assert not self.export.exists()

    def test_clear_keeps_the_handle_for_the_record(self):
        self.user.clear()
        assert "del@test.com" in self.user.first_name
        assert self.user.last_name == "del_user"


@pytest.mark.django_db(databases="__all__")
class TestReconcileDeletedUsers:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="rec@test.com", username="rec_user")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Reconcile Book")
        Mark(self.identity, self.book).update(ShelfType.WISHLIST, "c", 5, [], 0)

    def test_reconcile_clears_data_a_lost_job_left_behind(self):
        """Takahe deleted the identity, but our queued clear never ran."""
        self.user.clear()
        Identity.objects.filter(pk=self.identity.pk).update(deleted=timezone.now())
        assert ShelfMember.objects.filter(owner=self.identity).exists()
        cleared, requested = reconcile_deleted_users()
        assert (cleared, requested) == (1, 0)
        assert not ShelfMember.objects.filter(owner=self.identity).exists()
        self.identity.refresh_from_db()
        assert self.identity.deleted is not None

    def test_reconcile_clears_the_user_when_takahe_started_the_deletion(self):
        """Deletion began in Takahe and the identity_deleted callback was lost.

        Nothing cleared the user, so recovery has to do that too.
        """
        EmailAccount.objects.create(
            handle="rec@test.com", uid="rec", domain="test.com", user=self.user
        )
        NdjsonExporter.objects.create(user=self.user, metadata={})
        Identity.objects.filter(pk=self.identity.pk).update(deleted=timezone.now())
        cleared, _ = reconcile_deleted_users()
        assert cleared == 1
        self.user.refresh_from_db()
        assert not self.user.is_active
        assert not self.user.social_accounts.exists()
        assert not Task.objects.filter(user=self.user).exists()
        assert not ShelfMember.objects.filter(owner=self.identity).exists()

    def test_reconcile_re_requests_a_lost_takahe_deletion(self):
        """Our data is gone, but the Takahe identity is still live."""
        self.user.clear()
        self.identity.clear()
        cleared, requested = reconcile_deleted_users()
        assert (cleared, requested) == (0, 1)

    def test_reconcile_skips_a_suspended_account(self):
        self.user.is_active = False
        self.user.save()
        # even one an admin gave a last name, which is not a deletion marker
        self.user.last_name = "Someone"
        self.user.save()
        assert reconcile_deleted_users() == (0, 0)
        assert ShelfMember.objects.filter(owner=self.identity).exists()

    def test_reconcile_skips_an_account_already_cleared(self):
        self.user.clear()
        self.identity.clear()
        # the mirrored schema carries no behaviour, so stamp the row as
        # Takahe's own mark_deleted() would
        Identity.objects.filter(pk=self.identity.pk).update(deleted=timezone.now())
        assert reconcile_deleted_users() == (0, 0)
        assert self.identity.deleted is not None
        assert timezone.now() >= self.identity.deleted


@pytest.mark.django_db(databases="__all__")
class TestClearDataView:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="view@test.com", username="view_user")
        EmailAccount.objects.create(
            handle="view@test.com", uid="view", domain="test.com", user=self.user
        )
        self.client = Client()
        self.client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        self.url = reverse("users:clear_data")

    def test_a_handle_in_another_case_still_confirms(self):
        self.client.post(self.url, {"verification": " VIEW@Test.com "})
        self.user.refresh_from_db()
        assert not self.user.is_active
        assert self.user.last_name == self.user.username

    def test_a_wrong_handle_does_not_delete(self):
        self.client.post(self.url, {"verification": "someone@else.com"})
        self.user.refresh_from_db()
        assert self.user.is_active
        assert self.user.last_name == ""


@pytest.mark.django_db(databases="__all__")
def test_a_pending_task_blocks_deletion():
    user = User.register(email="busy@test.com", username="busy_user")
    EmailAccount.objects.create(
        handle="busy@test.com", uid="busy", domain="test.com", user=user
    )
    NdjsonExporter.objects.create(user=user, metadata={})
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    client.post(reverse("users:clear_data"), {"verification": "busy@test.com"})
    user.refresh_from_db()
    assert user.is_active
    assert user.last_name == ""


@pytest.mark.django_db(databases="__all__")
def test_identity_deleted_clears_a_suspended_user():
    """A suspended account deleted on the Takahe side is still cleared."""
    user = User.register(email="susp@test.com", username="susp_user")
    EmailAccount.objects.create(
        handle="susp@test.com", uid="susp", domain="test.com", user=user
    )
    user.is_active = False
    user.save()
    identity_deleted(user.identity.pk)
    user.refresh_from_db()
    assert user.last_name == "susp_user"
    assert not user.social_accounts.exists()
