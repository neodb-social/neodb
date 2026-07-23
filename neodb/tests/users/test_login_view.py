import json
import struct
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from altcha import Challenge, Payload, Solution, derive_key_pbkdf2, solve_challenge
from django.test import Client, override_settings
from django.urls import reverse

from common.models import SiteConfig
from mastodon.models import (
    Bluesky,
    BlueskyAccount,
    Email,
    Mastodon,
    MastodonAccount,
    Threads,
)
from users import login_proof
from users.models import User

SECURITY_ERROR = b"Security check failed. Please try again."


@pytest.fixture
def proof(monkeypatch: pytest.MonkeyPatch) -> Callable[[Client, str], str]:
    monkeypatch.setattr(login_proof, "LOGIN_PROOF_COST", 1)
    monkeypatch.setattr(login_proof, "LOGIN_PROOF_COUNTER_MIN", 2)
    monkeypatch.setattr(login_proof, "LOGIN_PROOF_COUNTER_MAX", 2)

    def issue(client: Client, method: str) -> str:
        response = client.get(reverse("users:login_proof"), {"method": method})
        assert response.status_code == 200
        challenge = Challenge.from_dict(response.json())
        solution = solve_challenge(challenge)
        assert solution is not None
        return Payload(challenge, solution).to_base64()

    return issue


@pytest.fixture
def mastodon_login_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    disabled = SiteConfig.system.model_copy(update={"enable_login_mastodon": False})
    monkeypatch.setattr(SiteConfig, "system", disabled)
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)


@pytest.mark.django_db(databases="__all__")
class TestLoginMethodSelection:
    def test_no_method(self, client):
        response = client.get(reverse("users:login"))
        assert response.status_code == 200
        assert response.context["selected_method"] == ""

    def test_bluesky_method(self, client):
        response = client.get(reverse("users:login"), {"method": "bluesky"})
        assert response.status_code == 200
        assert response.context["selected_method"] == "bluesky"
        assert b"var selected_method = 'bluesky'" in response.content

    def test_atproto_aliased_to_bluesky(self, client):
        # old notification messages persisted reauth URLs with ?method=atproto
        response = client.get(reverse("users:login"), {"method": "atproto"})
        assert response.status_code == 200
        assert response.context["selected_method"] == "bluesky"

    def test_unknown_method_ignored(self, client):
        response = client.get(reverse("users:login"), {"method": "x'</script>"})
        assert response.status_code == 200
        assert response.context["selected_method"] == ""

    def test_username_prefilled(self, client):
        response = client.get(
            reverse("users:login"),
            {"method": "bluesky", "username": "alice.bsky.social"},
        )
        assert response.status_code == 200
        assert response.context["selected_username"] == "alice.bsky.social"
        assert b"var selected_username = 'alice.bsky.social'" in response.content

    def test_invalid_username_ignored(self, client):
        response = client.get(
            reverse("users:login"),
            {"method": "bluesky", "username": "x'</script>@example.com"},
        )
        assert response.status_code == 200
        assert response.context["selected_username"] == ""

    def test_disabled_mastodon_is_not_offered(
        self, client, mastodon_login_disabled: None
    ) -> None:
        response = client.get(
            reverse("users:login"),
            {"method": "mastodon", "domain": "mastodon.online"},
        )

        assert response.status_code == 200
        assert response.context["enable_mastodon"] is False
        assert response.context["selected_method"] == ""
        assert response.context["selected_domain"] == ""
        assert b'id="platform-mastodon"' not in response.content
        assert b'id="login-mastodon"' not in response.content

    def test_disabled_mastodon_preserves_another_selected_method(
        self, client, mastodon_login_disabled: None
    ) -> None:
        response = client.get(
            reverse("users:login"),
            {"method": "email", "domain": "mastodon.online"},
        )

        assert response.status_code == 200
        assert response.context["selected_method"] == "email"
        assert response.context["selected_domain"] == ""


@pytest.mark.django_db(databases="__all__")
class TestReauthorizeUrl:
    def test_bluesky_points_to_bluesky_login_form(self):
        user = User.register(email="reauth@example.com", username="reauthuser")
        account = BlueskyAccount.objects.create(
            handle="reauth.bsky.social", user=user, domain="bsky.social", uid="1"
        )
        assert account.get_reauthorize_url() == reverse("users:login") + (
            "?method=bluesky&username=reauth.bsky.social"
        )

    def test_mastodon_points_to_oauth_flow(self):
        user = User.register(email="reauth2@example.com", username="reauthuser2")
        account = MastodonAccount.objects.create(
            handle="reauthuser2@mast.social",
            user=user,
            domain="mast.social",
            uid="2",
        )
        assert account.get_reauthorize_url() == reverse("mastodon:login") + (
            "?domain=mast.social"
        )


@pytest.mark.django_db(databases="__all__")
class TestLoginProof:
    def test_login_page_uses_invisible_proof_widget(self, client):
        with override_settings(ENABLE_LOGIN_EMAIL=True):
            response = client.get(reverse("users:login"))
        assert response.status_code == 200
        assert b"altcha@3.2.1/dist/main/altcha.min.js" in response.content
        assert response.content.count(b"<altcha-widget") >= 3
        assert b"captcha/" not in response.content
        assert b"Checking your browser..." in response.content
        assert b"fa-circle-nodes fa-spin-snap-8" in response.content
        assert b"catch (err)" in response.content

    def test_challenge_is_signed_bound_and_not_cacheable(self, client, proof):
        response = client.get(reverse("users:login_proof"), {"method": "mastodon"})
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store, private"
        challenge = Challenge.from_dict(response.json())
        assert challenge.signature
        assert challenge.parameters.algorithm == "PBKDF2/SHA-256"
        assert challenge.parameters.cost == 1
        assert challenge.parameters.expires_at is not None
        challenge_data = challenge.parameters.data
        assert challenge_data is not None
        assert challenge_data["method"] == "mastodon"
        assert len(challenge_data["session"]) == 64

    def test_unknown_challenge_method_rejected(self, client):
        response = client.get(reverse("users:login_proof"), {"method": "unknown"})
        assert response.status_code == 400

    def test_disabled_mastodon_challenge_rejected(
        self, client, mastodon_login_disabled: None
    ) -> None:
        response = client.get(reverse("users:login_proof"), {"method": "mastodon"})

        assert response.status_code == 400
        assert response.json()["error"] == "Mastodon login is disabled"

    def test_missing_and_malformed_proofs_rejected(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(
            Email,
            "send_login_email",
            lambda request, email, action: calls.append((email, action)),
        )
        missing = client.post(
            reverse("mastodon:email_login"), {"email": "alice@example.org"}
        )
        malformed = client.post(
            reverse("mastodon:email_login"),
            {"email": "alice@example.org", "altcha": "not-base64"},
        )
        assert SECURITY_ERROR in missing.content
        assert SECURITY_ERROR in malformed.content
        assert calls == []

    def test_verified_payload_with_missing_fields_is_rejected(
        self, client, proof, monkeypatch
    ):
        response = client.get(reverse("users:login_proof"), {"method": "email"})
        challenge = Challenge.from_dict(response.json())
        malformed_payload = SimpleNamespace(
            challenge=SimpleNamespace(
                parameters=SimpleNamespace(
                    algorithm=challenge.parameters.algorithm,
                    cost=challenge.parameters.cost,
                    data=challenge.parameters.data,
                    key_prefix=challenge.parameters.key_prefix,
                ),
                signature=challenge.signature,
            ),
            solution=SimpleNamespace(counter=2, derived_key="00" * 32),
        )
        monkeypatch.setattr(
            login_proof.Payload,
            "from_base64",
            lambda encoded: malformed_payload,
        )
        monkeypatch.setattr(
            login_proof,
            "verify_solution",
            lambda payload, secret: SimpleNamespace(verified=True),
        )

        response = client.post(
            reverse("mastodon:email_login"),
            {"email": "alice@example.org", "altcha": "malformed-but-verified"},
        )
        assert response.status_code == 200
        assert SECURITY_ERROR in response.content

    def test_valid_proof_is_accepted_once(self, client, proof, monkeypatch):
        calls = []
        monkeypatch.setattr(
            Email,
            "send_login_email",
            lambda request, email, action: calls.append((email, action)),
        )
        payload = proof(client, "email")
        data = {"email": "alice@example.org", "altcha": payload}
        accepted = client.post(reverse("mastodon:email_login"), data)
        replayed = client.post(reverse("mastodon:email_login"), data)
        assert b"Verification email is being sent" in accepted.content
        assert SECURITY_ERROR in replayed.content
        assert calls == [("alice@example.org", "login")]

    def test_proof_is_bound_to_session_and_method(self, client, proof, monkeypatch):
        threads_calls = []
        monkeypatch.setattr(
            Threads,
            "generate_auth_url",
            lambda request: threads_calls.append(request) or "https://threads.example/",
        )
        payload = proof(client, "email")
        other_client = Client()
        wrong_session = other_client.post(
            reverse("mastodon:email_login"),
            {"email": "alice@example.org", "altcha": payload},
        )
        wrong_method = client.post(
            reverse("mastodon:threads_login"), {"altcha": payload}
        )
        assert SECURITY_ERROR in wrong_session.content
        assert SECURITY_ERROR in wrong_method.content
        assert threads_calls == []

    def test_any_counter_does_not_bypass_work(self, client, proof):
        response = client.get(reverse("users:login_proof"), {"method": "email"})
        challenge = Challenge.from_dict(response.json())
        parameters = challenge.parameters
        counter = 0
        password = bytes.fromhex(parameters.nonce) + struct.pack(">I", counter)
        derived_key = derive_key_pbkdf2(
            parameters, bytes.fromhex(parameters.salt), password
        ).hex()
        payload = Payload(
            challenge, Solution(counter=counter, derived_key=derived_key)
        ).to_base64()
        response = client.post(
            reverse("mastodon:email_login"),
            {"email": "alice@example.org", "altcha": payload},
        )
        assert SECURITY_ERROR in response.content

    def test_expired_proof_rejected(self, client, proof, monkeypatch):
        monkeypatch.setattr(login_proof, "LOGIN_PROOF_TTL", -1)
        payload = proof(client, "email")
        response = client.post(
            reverse("mastodon:email_login"),
            {"email": "alice@example.org", "altcha": payload},
        )
        assert SECURITY_ERROR in response.content

    def test_mastodon_threads_and_bluesky_accept_valid_proofs(
        self, client, proof, monkeypatch
    ):
        enabled = SiteConfig.system.model_copy(update={"enable_login_bluesky": True})
        monkeypatch.setattr(SiteConfig, "system", enabled)
        monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
        calls = []
        monkeypatch.setattr(
            Mastodon,
            "generate_auth_url",
            lambda domain, request: (
                calls.append(("mastodon", domain)) or "https://mastodon.example/"
            ),
        )
        monkeypatch.setattr(
            Threads,
            "generate_auth_url",
            lambda request: (
                calls.append(("threads", None)) or "https://threads.example/"
            ),
        )
        monkeypatch.setattr(
            Bluesky,
            "generate_auth_url",
            lambda handle, request: (
                calls.append(("bluesky", handle)) or "https://pds.example/authorize"
            ),
        )

        mastodon = client.post(
            reverse("mastodon:login"),
            {"domain": "mastodon.online", "altcha": proof(client, "mastodon")},
        )
        threads = client.post(
            reverse("mastodon:threads_login"),
            {"altcha": proof(client, "threads")},
        )
        bluesky = client.post(
            reverse("mastodon:bluesky_login"),
            {
                "username": "alice.bsky.social",
                "altcha": proof(client, "bluesky"),
            },
        )
        assert mastodon.status_code == 302
        assert threads.status_code == 302
        assert bluesky.status_code == 302
        assert bluesky.url == "https://pds.example/authorize"
        assert calls == [
            ("mastodon", "mastodon.online"),
            ("threads", None),
            ("bluesky", "alice.bsky.social"),
        ]

    def test_passkey_options_accept_json_proof(self, client, proof):
        missing = client.post(
            reverse("users:passkey_login_options"),
            data=json.dumps({}),
            content_type="application/json",
        )
        payload = proof(client, "passkey")
        accepted = client.post(
            reverse("users:passkey_login_options"),
            data=json.dumps({"altcha": payload}),
            content_type="application/json",
        )
        assert missing.status_code == 400
        assert missing.json()["error"] == "Security check failed. Please try again."
        assert accepted.status_code == 200
        assert "challenge" in accepted.json()

    def test_anonymous_mastodon_get_returns_to_protected_form(self, client):
        response = client.get(reverse("mastodon:login"), {"domain": "mastodon.online"})
        assert response.status_code == 302
        assert response.url == (
            reverse("users:login") + "?method=mastodon&domain=mastodon.online"
        )

    def test_disabled_mastodon_login_is_rejected(
        self, client, mastodon_login_disabled: None
    ) -> None:
        response = client.get(reverse("mastodon:login"), {"domain": "example.org"})

        assert response.status_code == 200
        assert b"Mastodon login is disabled." in response.content

    def test_disabled_mastodon_oauth_is_rejected(
        self, client, mastodon_login_disabled: None
    ) -> None:
        response = client.get(reverse("mastodon:oauth"), {"code": "oauth-code"})

        assert response.status_code == 200
        assert b"Mastodon login is disabled." in response.content

    def test_disabled_mastodon_whitelist_does_not_block_email_registration(
        self, client, mastodon_login_disabled: None
    ) -> None:
        SiteConfig.system.mastodon_login_whitelist = ["mastodon.online"]
        account = Email.new_account("register@example.org")
        assert account is not None
        session = client.session
        session["verified_account"] = account.to_dict()
        session.save()

        response = client.get(reverse("users:register"))

        assert response.status_code == 200
        assert response.context["email_readonly"] is True

    def test_enabled_mastodon_whitelist_blocks_email_registration(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured = SiteConfig.system.model_copy(
            update={
                "enable_login_mastodon": True,
                "mastodon_login_whitelist": ["mastodon.online"],
            }
        )
        monkeypatch.setattr(SiteConfig, "system", configured)
        monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
        account = Email.new_account("register@example.org")
        assert account is not None
        session = client.session
        session["verified_account"] = account.to_dict()
        session.save()

        response = client.get(reverse("users:register"))

        assert response.status_code == 302
        assert response.url == reverse("common:home")

    def test_authenticated_mastodon_reconnect_bypasses_proof(
        self, client, monkeypatch, mastodon_login_disabled: None
    ):
        user = User.register(email="pow@example.com", username="powuser")
        client.force_login(user)
        monkeypatch.setattr(
            Mastodon,
            "generate_auth_url",
            lambda domain, request: "https://mastodon.example/",
        )
        response = client.post(
            reverse("mastodon:reconnect"), {"domain": "mastodon.online"}
        )
        assert response.status_code == 302
        assert response.url == "https://mastodon.example/"


@pytest.mark.django_db(databases="__all__")
class TestRegisterBlueskyRecordsPreference:
    def _prime_bluesky(self, client: Client) -> None:
        account = BlueskyAccount(
            uid="did:plc:reguser", domain="-", handle="reg.bsky.social"
        )
        session = client.session
        session["verified_account"] = account.to_dict()
        session.save()

    def test_option_offered_and_defaults_on_for_bluesky(self, client):
        self._prime_bluesky(client)
        response = client.get(reverse("users:register"))
        assert response.status_code == 200
        assert response.context["bluesky_register"] is True
        assert b"pref_bluesky_publish_records" in response.content

        response = client.post(
            reverse("users:register"),
            {"username": "regbsky", "email": "", "pref_bluesky_publish_records": "1"},
        )
        assert response.status_code == 200
        user = User.objects.get(username="regbsky")
        assert user.preference.bluesky_publish_records is True

    def test_option_can_be_unchecked(self, client):
        self._prime_bluesky(client)
        response = client.post(
            reverse("users:register"), {"username": "regbsky2", "email": ""}
        )
        assert response.status_code == 200
        user = User.objects.get(username="regbsky2")
        assert user.preference.bluesky_publish_records is False

    def test_option_not_offered_for_other_platforms(self, client):
        account = Email.new_account("regmail@example.org")
        assert account is not None
        session = client.session
        session["verified_account"] = account.to_dict()
        session.save()

        response = client.get(reverse("users:register"))
        assert response.status_code == 200
        assert response.context["bluesky_register"] is False
        assert b"pref_bluesky_publish_records" not in response.content

        # a rogue value is ignored for non-Bluesky registrations
        response = client.post(
            reverse("users:register"),
            {
                "username": "regmail",
                "email": "regmail@example.org",
                "pref_bluesky_publish_records": "1",
            },
        )
        assert response.status_code == 200
        user = User.objects.get(username="regmail")
        assert user.preference.bluesky_publish_records is False
