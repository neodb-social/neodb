import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from mastodon.models import EmailAccount
from takahe.models import User as TakaheUser
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestCreateSuperuser:
    @override_settings(ENABLE_LOGIN_EMAIL=False)
    def test_refuses_when_email_login_disabled(self):
        with pytest.raises(CommandError, match="NEODB_EMAIL_URL"):
            call_command(
                "createsuperuser",
                interactive=False,
                username="rootadmin",
                email="root@example.org",
            )
        assert not User.objects.filter(username="rootadmin").exists()

    @override_settings(ENABLE_LOGIN_EMAIL=True)
    def test_creates_superuser_when_email_login_enabled(self):
        call_command(
            "createsuperuser",
            interactive=False,
            username="rootadmin",
            email="root@example.org",
        )
        user = User.objects.get(username="rootadmin")
        assert user.is_superuser
        assert not user.has_usable_password()
        assert TakaheUser.objects.get(pk=user.pk).admin
        assert EmailAccount.objects.filter(
            user=user, handle="root@example.org"
        ).exists()
