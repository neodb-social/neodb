import pytest
from django.test import Client
from django.urls import reverse

from common.models import SiteConfig
from mastodon.models import Email
from takahe.models import Invite
from takahe.utils import Takahe
from users.models import User

REGISTER_URL = reverse("users:register")
INVITE_ONLY = b"Registration is for invitation only"


@pytest.fixture
def invite_only(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = SiteConfig.system.model_copy(
        update={
            "invite_only": True,
            "enable_register_email": True,
            "mastodon_login_whitelist": [],
            "registration_captcha_items": 0,
        }
    )
    monkeypatch.setattr(SiteConfig, "system", configured)
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)


def _start_signup(client: Client, email: str, invite: str) -> None:
    account = Email.new_account(email)
    assert account is not None
    session = client.session
    session["verified_account"] = account.to_dict()
    session["invite"] = invite
    session.save()


@pytest.mark.django_db(databases="__all__")
class TestConsumeInvite:
    def test_decrements_remaining_uses(self) -> None:
        Invite.objects.create(token="multi", uses=3)
        Takahe.consume_invite("multi")
        assert Invite.objects.get(token="multi").uses == 2
        assert Takahe.verify_invite("multi")

    def test_last_use_leaves_a_spent_invite(self) -> None:
        Invite.objects.create(token="single", uses=1)
        Takahe.consume_invite("single")
        Takahe.consume_invite("single")
        assert Invite.objects.get(token="single").uses == 0
        assert not Takahe.verify_invite("single")

    def test_unlimited_invite_is_untouched(self) -> None:
        Invite.objects.create(token="open", uses=None)
        Takahe.consume_invite("open")
        Takahe.consume_invite("open")
        assert Invite.objects.get(token="open").uses is None
        assert Takahe.verify_invite("open")

    def test_unknown_or_empty_token_is_a_no_op(self) -> None:
        Invite.objects.create(token="other", uses=1)
        Takahe.consume_invite("")
        Takahe.consume_invite("missing")
        assert Invite.objects.get(token="other").uses == 1


@pytest.mark.django_db(databases="__all__")
@pytest.mark.usefixtures("invite_only")
class TestRegisterConsumesInvite:
    def test_single_use_invite_registers_one_account(self) -> None:
        Invite.objects.create(token="once", uses=1)

        first = Client()
        _start_signup(first, "alice@example.org", "once")
        first.post(REGISTER_URL, {"username": "alice"})
        assert User.objects.filter(username="alice").exists()
        assert Invite.objects.get(token="once").uses == 0

        second = Client()
        _start_signup(second, "bob@example.org", "once")
        response = second.post(REGISTER_URL, {"username": "bob"})
        assert INVITE_ONLY in response.content
        assert not User.objects.filter(username="bob").exists()

    def test_unlimited_invite_keeps_working(self) -> None:
        Invite.objects.create(token="always", uses=None)
        for name in ("carol", "dave"):
            client = Client()
            _start_signup(client, f"{name}@example.org", "always")
            client.post(REGISTER_URL, {"username": name})
            assert User.objects.filter(username=name).exists()
        assert Invite.objects.get(token="always").uses is None

    def test_failed_registration_keeps_the_use(self) -> None:
        User.register(username="erin")
        Invite.objects.create(token="kept", uses=1)
        client = Client()
        _start_signup(client, "erin@example.org", "kept")
        client.post(REGISTER_URL, {"username": "erin"})
        assert Invite.objects.get(token="kept").uses == 1
