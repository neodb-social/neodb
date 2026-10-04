import pytest
from django.test import Client
from django.urls import reverse

from common.models import SiteConfig
from mastodon.models import BlueskyAccount, Email, EmailAccount
from users.models import User

CLOSED_MESSAGE = b"Registration with email is not available"
REGISTER_URL = reverse("users:register")
CAPTCHA_URL = reverse("users:captcha")


def _configure(monkeypatch: pytest.MonkeyPatch, **updates) -> None:
    configured = SiteConfig.system.model_copy(update=updates)
    monkeypatch.setattr(SiteConfig, "system", configured)
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)


@pytest.fixture
def closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(
        monkeypatch,
        enable_register_email=False,
        invite_only=False,
        mastodon_login_whitelist=[],
        registration_captcha_items=0,
    )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record what would be emailed, and skip the login proof."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "mastodon.views.email.verify_login_proof", lambda request, method: True
    )
    monkeypatch.setattr(
        Email,
        "send_login_email",
        lambda request, email, action: calls.append((email, action)),
    )
    return calls


def _make_user(username: str, email: str) -> User:
    user = User.register(username=username)
    uid, domain = email.split("@", 1)
    EmailAccount.objects.create(handle=email, uid=uid, domain=domain, user=user)
    return user


def _verify_email(client: Client, email: str) -> None:
    account = Email.new_account(email)
    assert account is not None
    session = client.session
    session["verified_account"] = account.to_dict()
    session.save()


def test_enabled_by_default() -> None:
    assert SiteConfig.SystemOptions().enable_register_email is True


@pytest.mark.django_db(databases="__all__")
@pytest.mark.usefixtures("closed")
class TestEmailLoginView:
    def test_new_address_gets_no_code(self, client, sent) -> None:
        response = client.post(
            reverse("mastodon:email_login"), {"email": "alice@example.org"}
        )
        assert response.status_code == 200
        assert CLOSED_MESSAGE in response.content
        assert sent == []

    def test_unlinked_account_row_gets_no_code(self, client, sent) -> None:
        EmailAccount.objects.create(
            handle="dave@example.org", uid="dave", domain="example.org"
        )
        response = client.post(
            reverse("mastodon:email_login"), {"email": "dave@example.org"}
        )
        assert CLOSED_MESSAGE in response.content
        assert sent == []

    def test_existing_user_still_gets_a_code(self, client, sent) -> None:
        _make_user("bob", "bob@example.org")
        response = client.post(
            reverse("mastodon:email_login"), {"email": "Bob@Example.org"}
        )
        assert b"Verification email is being sent" in response.content
        assert sent == [("Bob@Example.org", "login")]

    def test_new_address_gets_a_code_when_enabled(
        self, client, sent, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, enable_register_email=True)
        response = client.post(
            reverse("mastodon:email_login"), {"email": "alice@example.org"}
        )
        assert b"Verification email is being sent" in response.content
        assert sent == [("alice@example.org", "login")]


@pytest.mark.django_db(databases="__all__")
@pytest.mark.usefixtures("closed")
class TestRegisterView:
    def test_pending_email_signup_is_refused(self, client) -> None:
        # e.g. a code sent before the option was turned off
        _verify_email(client, "alice@example.org")
        response = client.post(REGISTER_URL, {"username": "alice"})
        assert CLOSED_MESSAGE in response.content
        assert not User.objects.filter(username="alice").exists()

    def test_captcha_page_refuses_email_signup(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, registration_captcha_items=4)
        _verify_email(client, "alice@example.org")
        response = client.get(CAPTCHA_URL)
        assert CLOSED_MESSAGE in response.content

    def test_other_platform_can_register(self, client) -> None:
        account = BlueskyAccount(domain="-", uid="did:plc:closed", handle="c.example")
        session = client.session
        session["verified_account"] = account.to_dict()
        session.save()
        response = client.get(REGISTER_URL)
        assert response.status_code == 200
        assert CLOSED_MESSAGE not in response.content

    def test_logged_in_user_can_link_email(self, client, sent) -> None:
        user = User.register(username="carol")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.post(
            REGISTER_URL, {"username": "carol", "email": "carol@example.org"}
        )
        assert CLOSED_MESSAGE not in response.content
        assert sent == [("carol@example.org", "verify")]
