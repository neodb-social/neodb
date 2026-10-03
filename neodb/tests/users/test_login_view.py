import json
import struct
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
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
    MastodonApplication,
    Threads,
)
from users import login_proof
from users.models import User

SECURITY_ERROR = b"Security check failed. Please try again."


def _oauth_response(status: int, data: dict[str, object]) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data).encode()
    return response


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
    def test_bluesky_points_to_authorization_start(self):
        user = User.register(email="reauth@example.com", username="reauthuser")
        account = BlueskyAccount.objects.create(
            handle="reauth.bsky.social", user=user, domain="bsky.social", uid="1"
        )
        assert account.get_reauthorize_url() == reverse("mastodon:bluesky_login") + (
            "?username=reauth.bsky.social"
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


@pytest.mark.django_db(databases="__all__")
class TestMastodonOAuthCallback:
    @pytest.fixture(autouse=True)
    def setup_oauth(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        monkeypatch.setattr(SiteConfig.system, "enable_login_mastodon", True)
        monkeypatch.setattr(SiteConfig.system, "registration_captcha_items", 0)
        self.app = MastodonApplication.objects.create(
            domain_name="mastodon.online",
            api_domain="api.mastodon.online",
            server_version="4.5.0",
            app_id="1",
            client_id="oauth-client",
            client_secret="oauth-secret",
        )
        with (
            patch("mastodon.models.mastodon.post") as self.http_post,
            patch("mastodon.models.mastodon.get") as self.http_get,
            patch(
                "mastodon.models.mastodon.Takahe.fetch_remote_identity"
            ) as self.fetch,
            patch.object(User, "sync_accounts_later"),
        ):
            self.http_post.return_value = _oauth_response(
                200, {"access_token": "new-token", "refresh_token": "new-refresh"}
            )
            self.http_get.return_value = _oauth_response(
                200,
                {
                    "id": "7",
                    "username": "oauthuser",
                    "acct": "oauthuser",
                    "display_name": "OAuth user",
                    "url": "https://mastodon.online/@oauthuser",
                },
            )
            yield

    def _prime(
        self,
        client: Client,
        state: str | None = "oauth-state",
        domain: str | None = "mastodon.online",
    ) -> None:
        session = client.session
        if state is not None:
            session["mastodon_oauth_state"] = state
        if domain is not None:
            session["mastodon_domain"] = domain
        session.save()

    @pytest.mark.parametrize(
        ("expected", "actual"),
        [(None, "oauth-state"), ("oauth-state", None), ("oauth-state", "wrong")],
    )
    def test_invalid_state_never_exchanges_code(
        self, client: Client, expected: str | None, actual: str | None
    ) -> None:
        self._prime(client, state=expected)
        query = {"code": "oauth-code"}
        if actual is not None:
            query["state"] = actual
        response = client.get(reverse("mastodon:oauth"), query)
        assert response.status_code == 200
        assert b"Invalid OAuth state" in response.content
        assert "mastodon_oauth_state" not in client.session
        self.http_post.assert_not_called()
        self.http_get.assert_not_called()

    def test_missing_code_never_exchanges_token(self, client: Client) -> None:
        self._prime(client)
        response = client.get(reverse("mastodon:oauth"), {"state": "oauth-state"})
        assert b"Invalid response from Fediverse instance" in response.content
        self.http_post.assert_not_called()

    def test_missing_domain_rejects_callback(self, client: Client) -> None:
        self._prime(client, domain=None)
        response = client.get(
            reverse("mastodon:oauth"), {"code": "oauth-code", "state": "oauth-state"}
        )
        assert b"Invalid cookie data" in response.content
        self.http_post.assert_not_called()

    def test_unknown_instance_is_bad_request(self, client: Client) -> None:
        self._prime(client, domain="unknown.example")
        response = client.get(
            reverse("mastodon:oauth"), {"code": "oauth-code", "state": "oauth-state"}
        )
        assert response.status_code == 400
        self.http_post.assert_not_called()

    @pytest.mark.parametrize("failure", ["http", "json", "missing_token", "network"])
    def test_token_failure_does_not_verify_account(
        self, client: Client, failure: str
    ) -> None:
        self._prime(client)
        if failure == "http":
            self.http_post.return_value = _oauth_response(401, {"error": "invalid"})
        elif failure == "json":
            self.http_post.return_value = _oauth_response(200, {})
            self.http_post.return_value._content = b"not JSON"
        elif failure == "missing_token":
            self.http_post.return_value = _oauth_response(200, {})
        else:
            self.http_post.side_effect = requests.Timeout("token timeout")
        response = client.get(
            reverse("mastodon:oauth"), {"code": "oauth-code", "state": "oauth-state"}
        )
        assert b"Invalid token from Fediverse instance" in response.content
        self.http_get.assert_not_called()
        assert "_auth_user_id" not in client.session

    def test_invalid_credentials_do_not_login(self, client: Client) -> None:
        self._prime(client)
        self.http_get.return_value = _oauth_response(401, {"error": "revoked"})
        response = client.get(
            reverse("mastodon:oauth"), {"code": "oauth-code", "state": "oauth-state"}
        )
        assert b"Invalid account data from Fediverse instance" in response.content
        assert "_auth_user_id" not in client.session
        self.fetch.assert_not_called()

    def test_existing_account_logs_in_and_refreshes_tokens(
        self, client: Client
    ) -> None:
        user = User.register(username="oauthuser")
        account = MastodonAccount.objects.create(
            user=user, domain="mastodon.online", uid="7", handle="old@mastodon.online"
        )
        self._prime(client)
        session = client.session
        session["next_url"] = reverse("users:info")
        session.save()
        response = client.get(
            reverse("mastodon:oauth"), {"code": "oauth-code", "state": "oauth-state"}
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("users:info")
        assert client.session["_auth_user_id"] == str(user.pk)
        assert "next_url" not in client.session
        assert "mastodon_oauth_state" not in client.session
        account.refresh_from_db()
        assert account.access_token == "new-token"
        assert account.refresh_token == "new-refresh"
        assert account.account_data["username"] == "oauthuser"
        assert account.handle == "oauthuser@mastodon.online"
        assert self.http_post.call_args.args == (
            "https://api.mastodon.online/oauth/token",
        )
        payload = self.http_post.call_args.kwargs["data"]
        assert payload["code"] == "oauth-code"
        assert payload["client_id"] == "oauth-client"
        assert payload["redirect_uri"].endswith(reverse("mastodon:oauth"))
        assert self.http_get.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer new-token"
        )

    def test_new_account_registers_and_callback_cannot_be_replayed(
        self, client: Client
    ) -> None:
        self._prime(client)
        query = {"code": "oauth-code", "state": "oauth-state"}
        response = client.get(reverse("mastodon:oauth"), query)
        assert response.status_code == 302
        assert response["Location"] == reverse("users:register")
        verified = MastodonAccount.from_dict(client.session["verified_account"])
        assert verified is not None
        assert verified.handle == "oauthuser@mastodon.online"
        assert verified.access_token == "new-token"
        self.fetch.assert_called_once_with("oauthuser@mastodon.online")
        assert not MastodonAccount.objects.filter(uid="7").exists()
        replay = client.get(reverse("mastodon:oauth"), query)
        assert b"Invalid OAuth state" in replay.content
        assert self.http_post.call_count == 1
        assert self.http_get.call_count == 1

    def test_changed_remote_uid_keeps_existing_link(self, client: Client) -> None:
        user = User.register(username="uiduser")
        account = MastodonAccount.objects.create(
            user=user,
            domain="mastodon.online",
            uid="old-id",
            handle="oauthuser@mastodon.online",
        )
        self._prime(client)
        response = client.get(
            reverse("mastodon:oauth"), {"code": "oauth-code", "state": "oauth-state"}
        )
        assert response.status_code == 302
        account.refresh_from_db()
        assert account.uid == "7"
        assert account.user == user
        assert MastodonAccount.objects.filter(domain="mastodon.online").count() == 1

    def test_inactive_user_cannot_login(self, client: Client) -> None:
        user = User.register(username="inactiveoauth")
        User.objects.filter(pk=user.pk).update(is_active=False)
        MastodonAccount.objects.create(
            user=user,
            domain="mastodon.online",
            uid="7",
            handle="oauthuser@mastodon.online",
        )
        self._prime(client)
        response = client.get(
            reverse("mastodon:oauth"), {"code": "oauth-code", "state": "oauth-state"}
        )
        assert b"Invalid user" in response.content
        assert "_auth_user_id" not in client.session
