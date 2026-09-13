import pytest
from django.urls import reverse

from common.models import SiteConfig
from mastodon.models import Email, EmailAccount
from users.models import User
from users.views.account import RegistrationForm

BLOCKED_MESSAGE = b"Unable to register with this email address"
FORM_MESSAGE = "Email addresses from this domain are not accepted."


def _configure(monkeypatch: pytest.MonkeyPatch, **updates) -> None:
    configured = SiteConfig.system.model_copy(update=updates)
    monkeypatch.setattr(SiteConfig, "system", configured)
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)


@pytest.fixture
def blocklist(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, email_domain_blocklist=["blocked.example"])


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record what email_login would send, and skip the login proof."""
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


class TestNormalization:
    def test_entries_are_cleaned_up(self) -> None:
        options = SiteConfig.SystemOptions(
            email_domain_blocklist=[
                " @Example.COM ",
                "*.foo.test",
                "user@bar.test",
                "example.com",
                "",
            ]
        )
        assert options.email_domain_blocklist == [
            "example.com",
            "foo.test",
            "bar.test",
        ]

    def test_empty_by_default(self) -> None:
        assert SiteConfig.SystemOptions().email_domain_blocklist == []


@pytest.mark.usefixtures("blocklist")
class TestDomainMatching:
    @pytest.mark.parametrize(
        "email",
        [
            "alice@blocked.example",
            "alice@BLOCKED.example",
            "alice@mail.blocked.example",
        ],
    )
    def test_blocked(self, email: str) -> None:
        assert Email.is_domain_blocked(email) is True

    @pytest.mark.parametrize(
        "email",
        [
            "alice@example.org",
            "alice@notblocked.example",
            # a suffix that is not a domain boundary
            "alice@myblocked.example",
        ],
    )
    def test_allowed(self, email: str) -> None:
        assert Email.is_domain_blocked(email) is False

    def test_empty_blocklist_allows_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, email_domain_blocklist=[])
        assert Email.is_domain_blocked("alice@blocked.example") is False


@pytest.mark.django_db(databases="__all__")
@pytest.mark.usefixtures("blocklist")
class TestRegistrationBlocked:
    def test_new_address_is_blocked(self) -> None:
        assert Email.is_registration_blocked("alice@blocked.example") is True

    def test_existing_user_is_not_blocked(self) -> None:
        _make_user("bob", "bob@blocked.example")
        assert Email.is_registration_blocked("bob@blocked.example") is False

    def test_existing_user_match_is_case_insensitive(self) -> None:
        _make_user("carol", "carol@blocked.example")
        assert Email.is_registration_blocked("Carol@Blocked.Example") is False

    def test_unlinked_account_row_is_still_blocked(self) -> None:
        EmailAccount.objects.create(
            handle="dave@blocked.example", uid="dave", domain="blocked.example"
        )
        assert Email.is_registration_blocked("dave@blocked.example") is True


@pytest.mark.django_db(databases="__all__")
@pytest.mark.usefixtures("blocklist")
class TestEmailLoginView:
    def test_new_address_gets_no_code(self, client, sent) -> None:
        response = client.post(
            reverse("mastodon:email_login"), {"email": "alice@blocked.example"}
        )
        assert response.status_code == 200
        assert BLOCKED_MESSAGE in response.content
        assert sent == []

    def test_subdomain_gets_no_code(self, client, sent) -> None:
        response = client.post(
            reverse("mastodon:email_login"), {"email": "alice@mail.blocked.example"}
        )
        assert BLOCKED_MESSAGE in response.content
        assert sent == []

    def test_existing_user_still_gets_a_code(self, client, sent) -> None:
        _make_user("bob", "bob@blocked.example")
        response = client.post(
            reverse("mastodon:email_login"), {"email": "bob@blocked.example"}
        )
        assert b"Verification email is being sent" in response.content
        assert sent == [("bob@blocked.example", "login")]

    def test_other_domain_is_unaffected(self, client, sent) -> None:
        response = client.post(
            reverse("mastodon:email_login"), {"email": "alice@example.org"}
        )
        assert b"Verification email is being sent" in response.content
        assert sent == [("alice@example.org", "login")]


@pytest.mark.django_db(databases="__all__")
@pytest.mark.usefixtures("blocklist")
class TestRegistrationForm:
    def test_new_registration_is_refused(self) -> None:
        form = RegistrationForm({"username": "alice", "email": "alice@blocked.example"})
        assert not form.is_valid()
        assert FORM_MESSAGE in form.errors["email"]

    def test_linking_a_blocked_address_is_refused(self) -> None:
        user = _make_user("bob", "bob@example.org")
        form = RegistrationForm(
            {"username": "bob", "email": "bob@blocked.example"}, instance=user
        )
        assert not form.is_valid()
        assert FORM_MESSAGE in form.errors["email"]

    def test_address_already_linked_is_kept(self) -> None:
        user = _make_user("carol", "carol@blocked.example")
        form = RegistrationForm(
            {"username": "carol", "email": "carol@blocked.example"}, instance=user
        )
        assert form.is_valid(), form.errors

    def test_other_domain_is_accepted(self) -> None:
        form = RegistrationForm({"username": "dave", "email": "dave@example.org"})
        assert form.is_valid(), form.errors


@pytest.mark.django_db(databases="__all__")
class TestAdminRoundTrip:
    def test_saved_value_is_normalized_on_load(self) -> None:
        old_system = getattr(SiteConfig, "system", None)
        try:
            SiteConfig.set_system(
                email_domain_blocklist=["@Blocked.Example", "*.spam.test"]
            )

            SiteConfig.reload()

            assert SiteConfig.system.email_domain_blocklist == [
                "blocked.example",
                "spam.test",
            ]
            assert Email.is_domain_blocked("alice@mail.spam.test") is True
        finally:
            SiteConfig.objects.filter(pk=1).delete()
            if old_system is not None:
                SiteConfig.system = old_system
                SiteConfig._apply_to_settings(old_system)
