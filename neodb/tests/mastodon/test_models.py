import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import pytest
import requests
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import RequestAborted
from django.db import IntegrityError
from django.utils import timezone
from django.test import RequestFactory

from common.models import SiteConfig
from journal.models.common import VisibilityType
from mastodon.models import Mastodon, MastodonAccount, MastodonApplication, Platform
from mastodon.models.mastodon import (
    TootVisibilityEnum,
    _force_recreate_app,
    _get_redirect_uris,
    _get_scopes,
    _response_error,
    get_toot_visibility,
    get_or_create_fediverse_application,
    detect_server_info,
)
from users.models import User


class TestGetScopes:
    def test_pixelfed_gets_legacy_scope(self):
        assert (
            _get_scopes("3.5.5 (compatible; Pixelfed 0.11.4)")
            == settings.MASTODON_LEGACY_CLIENT_SCOPE
        )

    def test_friendica_gets_legacy_scope(self):
        assert _get_scopes("Friendica 2023.05") == settings.MASTODON_LEGACY_CLIENT_SCOPE

    def test_mastodon_gets_modern_scope(self):
        assert _get_scopes("4.1.0") == settings.MASTODON_CLIENT_SCOPE

    def test_empty_version_gets_modern_scope(self):
        assert _get_scopes("") == settings.MASTODON_CLIENT_SCOPE

    def test_gotosocial_gets_modern_scope(self):
        assert _get_scopes("0.13.1") == settings.MASTODON_CLIENT_SCOPE


class TestForceRecreateApp:
    def test_sharkey_triggers_recreate(self):
        assert _force_recreate_app("Misskey(Sharkey) 2023.12.0")

    def test_firefish_triggers_recreate(self):
        assert _force_recreate_app("1.0.0-dev42 (Firefish)")

    def test_mastodon_does_not_trigger(self):
        assert not _force_recreate_app("4.1.0")

    def test_empty_does_not_trigger(self):
        assert not _force_recreate_app("")

    def test_none_does_not_trigger(self):
        assert not _force_recreate_app(None)

    def test_partial_name_does_not_trigger(self):
        # Requires characters before AND after the keyword
        assert not _force_recreate_app("Sharkey")


class TestGetRedirectUris:
    def test_contains_site_url(self):
        result = _get_redirect_uris("4.1.0")
        assert settings.SITE_INFO["site_url"] in result

    def test_pixelfed_returns_single_uri(self):
        result = _get_redirect_uris("3.5.5 (compatible; Pixelfed 0.11.4)")
        # Pixelfed does not support multiple redirect URIs
        assert "\n" not in result

    def test_modern_may_have_multiple_uris(self):
        # Modern servers support multiple URIs; result is \n-separated
        result = _get_redirect_uris("4.1.0")
        # At minimum, the primary site URL is included
        assert settings.SITE_INFO["site_url"] + "/account/login/oauth" in result

    def test_mitra_returns_single_uri(self, monkeypatch):
        monkeypatch.setattr(
            SiteConfig.system, "alternative_domains", ["alt.example.org"]
        )
        result = _get_redirect_uris("4.0.0 (compatible; Mitra 5.10.0)")
        assert result == settings.SITE_INFO["site_url"] + "/account/login/oauth"

    def test_alternative_domains_are_cleaned(self, monkeypatch):
        primary = settings.SITE_INFO["site_url"] + "/account/login/oauth"
        monkeypatch.setattr(
            SiteConfig.system,
            "alternative_domains",
            ["", "  ", settings.SITE_DOMAIN, "Alt.Example.org", "alt.example.org"],
        )
        result = _get_redirect_uris("4.1.0")
        assert result.split("\n") == [
            primary,
            "https://alt.example.org/account/login/oauth",
        ]


class TestResponseError:
    def test_json_error_description(self):
        response = Mock(
            json=Mock(return_value={"error": "x", "error_description": "invalid uri"})
        )
        assert _response_error(response) == "invalid uri"

    def test_json_error_only(self):
        response = Mock(json=Mock(return_value={"error": "bad"}))
        assert _response_error(response) == "bad"

    def test_non_json_body(self):
        response = Mock(json=Mock(side_effect=ValueError), text="<html>oops</html>")
        assert _response_error(response) == "<html>oops</html>"

    def test_json_list(self):
        response = Mock(json=Mock(return_value=[1]))
        assert _response_error(response) == ""


@pytest.mark.django_db(databases="__all__")
class TestGetTootVisibility:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(username="visuser")

    def test_visibility_2_returns_direct(self):
        assert get_toot_visibility(2, self.user) == TootVisibilityEnum.DIRECT

    def test_visibility_1_returns_private(self):
        assert get_toot_visibility(1, self.user) == TootVisibilityEnum.PRIVATE

    def test_visibility_0_public_mode_0_returns_public(self):
        self.user.preference.post_public_mode = 0
        self.user.preference.save()
        assert get_toot_visibility(0, self.user) == TootVisibilityEnum.PUBLIC

    def test_visibility_0_public_mode_1_returns_unlisted(self):
        self.user.preference.post_public_mode = 1
        self.user.preference.save()
        assert get_toot_visibility(0, self.user) == TootVisibilityEnum.UNLISTED


@pytest.mark.django_db(databases="__all__")
class TestMastodonAccount:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(username="mstuser")
        self.account = MastodonAccount.objects.create(
            handle="mstuser@social.example",
            user=self.user,
            domain="social.example",
            uid="12345",
        )

    def test_platform_is_mastodon(self):
        assert self.account.platform == Platform.MASTODON

    def test_str_includes_handle(self):
        assert "mstuser" in str(self.account)

    def test_to_dict_contains_basic_fields(self):
        d = self.account.to_dict()
        assert d["uid"] == "12345"
        assert d["domain"] == "social.example"
        assert d["handle"] == "mstuser@social.example"

    def test_to_dict_excludes_datetime_fields(self):
        d = self.account.to_dict()
        assert "created" not in d
        assert "modified" not in d
        assert "last_refresh" not in d
        assert "last_reachable" not in d

    def test_from_dict_reconstructs_object(self):
        d = self.account.to_dict()
        reconstructed = MastodonAccount.from_dict(d)
        assert reconstructed is not None
        assert reconstructed.uid == "12345"
        assert reconstructed.domain == "social.example"

    def test_from_dict_none_returns_none(self):
        assert MastodonAccount.from_dict(None) is None

    def test_check_alive_returns_false_without_network(self):
        # check_alive tries webfinger; with no real server it returns False
        # We verify the base class default, not the subclass override
        from mastodon.models.common import SocialAccount

        base = SocialAccount()
        assert base.check_alive() is False

    def test_sync_skips_when_recently_refreshed(self):
        from django.utils import timezone

        self.account.last_refresh = timezone.now()
        # sync returns False when last_refresh is recent (sleep_hours=0 is exceeded immediately)
        # The base SocialAccount.sync() would return False since check_alive() is False
        # MastodonAccount.check_alive() uses network, but sync skips via sleep_hours logic
        result = self.account.sync(skip_graph=True, sleep_hours=24)
        assert result is False


@pytest.mark.django_db(databases="__all__")
class TestSocialAccountSaveFields:
    """A sync job holds the instance across network calls, so the user may
    disconnect the account before it writes back."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(username="raceuser")
        self.account = MastodonAccount.objects.create(
            handle="raceuser@social.example",
            user=self.user,
            domain="social.example",
            uid="54321",
        )

    def test_save_fields_persists_normally(self):
        self.account.handle = "renamed@social.example"
        assert self.account.save_fields("handle") is True
        self.account.refresh_from_db()
        assert self.account.handle == "renamed@social.example"

    def test_save_fields_tolerates_concurrent_delete(self):
        MastodonAccount.objects.filter(pk=self.account.pk).delete()
        self.account.handle = "gone@social.example"
        assert self.account.save_fields("handle") is False
        assert self.account.pk is None

    def test_save_fields_noop_once_pk_cleared(self):
        self.account.pk = None
        assert self.account.save_fields("handle") is False

    def test_save_fields_propagates_real_database_errors(self):
        MastodonAccount.objects.create(
            handle="taken@social.example",
            user=User.register(username="raceuser2"),
            domain="social.example",
            uid="99999",
        )
        self.account.handle = "taken@social.example"
        with pytest.raises(IntegrityError):
            self.account.save_fields("handle")

    def test_refresh_graph_tolerates_concurrent_delete(self):
        with patch.object(MastodonAccount, "get_related_accounts", return_value=[]):
            MastodonAccount.objects.filter(pk=self.account.pk).delete()
            self.account.refresh_graph()
        assert self.account.pk is None

    def test_sync_graph_skips_deleted_account(self):
        # sync_accounts() runs a second pass over the same instances, so a
        # stale graph must not be imported for an account the user removed
        other = MastodonAccount.objects.create(
            handle="friend@social.example",
            user=User.register(username="racefriend"),
            domain="social.example",
            uid="77777",
        )
        self.account.following = [other.handle]

        with patch("mastodon.models.mastodon.Takahe.follow") as follow:
            assert self.account.sync_graph() == 1
            follow.assert_called_once()

            follow.reset_mock()
            self.account.pk = None
            assert self.account.sync_graph() == 0
            follow.assert_not_called()

    def test_sync_does_not_record_failure_for_deleted_account(self):
        def _refresh():
            MastodonAccount.objects.filter(pk=self.account.pk).delete()
            self.account.save_fields("last_refresh")
            return False

        with (
            patch.object(MastodonAccount, "check_alive", return_value=True),
            patch.object(MastodonAccount, "refresh", side_effect=_refresh),
            patch.object(MastodonAccount, "_record_account_failure") as record_fail,
            patch.object(MastodonAccount, "_emit_sync_result") as emit,
        ):
            assert self.account.sync() is False
        record_fail.assert_not_called()
        assert emit.call_args.args == ("skip_deleted",)

    def test_sync_stops_and_skips_graph_when_deleted_mid_sync(self):
        def _refresh():
            MastodonAccount.objects.filter(pk=self.account.pk).delete()
            self.account.last_refresh = timezone.now()
            self.account.save_fields("last_refresh")
            return True

        with (
            patch.object(MastodonAccount, "check_alive", return_value=True),
            patch.object(MastodonAccount, "refresh", side_effect=_refresh),
            patch.object(MastodonAccount, "refresh_graph") as refresh_graph,
        ):
            assert self.account.sync() is False
        refresh_graph.assert_not_called()


class TestDetectConfigurations:
    STAR_CODES = [
        settings.STAR_SOLID.strip(":"),
        settings.STAR_HALF.strip(":"),
        settings.STAR_EMPTY.strip(":"),
    ]

    def _response(self, status_code: int, json_data) -> Mock:
        response = Mock()
        response.status_code = status_code
        response.json.return_value = json_data
        return response

    def _detect(
        self,
        app: MastodonApplication,
        emoji_response: Mock,
        instance_response: Mock | None = None,
    ) -> None:
        if instance_response is None:
            instance_response = self._response(
                200, {"configuration": {"statuses": {"max_characters": 1000}}}
            )

        def fake_get(url, **kwargs):
            if url.endswith("/api/v1/instance"):
                return instance_response
            return emoji_response

        with patch("mastodon.models.mastodon.get", side_effect=fake_get):
            app.detect_configurations()

    def test_all_star_emojis_enable_custom_mode(self):
        app = MastodonApplication(domain_name="social.example")
        emojis = [{"shortcode": c} for c in self.STAR_CODES]
        self._detect(app, self._response(200, emojis))
        assert app.star_mode == 1

    def test_star_half_alone_keeps_unicode_mode(self):
        app = MastodonApplication(domain_name="social.example")
        self._detect(app, self._response(200, [{"shortcode": "star_half"}]))
        assert app.star_mode == 0

    def test_missing_emojis_reset_stale_custom_mode(self):
        app = MastodonApplication(domain_name="social.example", star_mode=1)
        self._detect(app, self._response(200, [{"shortcode": "stardewvalley"}]))
        assert app.star_mode == 0

    def test_unreachable_emoji_endpoint_keeps_existing_mode(self):
        app = MastodonApplication(domain_name="social.example", star_mode=1)
        self._detect(app, self._response(503, None))
        assert app.star_mode == 1

    def test_malformed_emoji_payload_resets_to_unicode(self):
        app = MastodonApplication(domain_name="social.example", star_mode=1)
        self._detect(app, self._response(200, {"error": "unexpected"}))
        assert app.star_mode == 0

    def test_max_status_len_updated_from_instance(self):
        app = MastodonApplication(domain_name="social.example")
        self._detect(app, self._response(200, []))
        assert app.max_status_len == 1000

    def test_malformed_instance_payload_keeps_max_status_len(self):
        app = MastodonApplication(domain_name="social.example")
        self._detect(
            app, self._response(200, []), instance_response=self._response(200, [])
        )
        assert app.max_status_len == 500

    def test_emoji_entries_without_string_shortcode_ignored(self):
        app = MastodonApplication(domain_name="social.example", star_mode=1)
        emojis = [{"shortcode": None}, {"url": "x"}, "junk"]
        self._detect(app, self._response(200, emojis))
        assert app.star_mode == 0


def _http_response(status_code: int, body: bytes) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = body
    response.encoding = "utf-8"
    return response


@pytest.mark.django_db(databases="__all__")
class TestMastodonAccountPost:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(username="postuser")
        self.account = MastodonAccount.objects.create(
            handle="postuser@social.example",
            user=self.user,
            domain="social.example",
            uid="54321",
        )

    def _post(self, response: requests.Response) -> dict:
        with patch("mastodon.models.mastodon.post_toot2", return_value=response):
            return self.account.post("hello", VisibilityType.Private)

    def test_valid_response_returns_id_and_url(self):
        url = "https://social.example/@postuser/1"
        r = _http_response(200, b'{"id": "1", "url": "' + url.encode() + b'"}')
        assert self._post(r) == {"id": "1", "url": url}

    def test_non_json_body_aborts(self):
        # a domain no longer running Mastodon may answer 200 with plain text
        r = _http_response(200, b"social.example")
        with pytest.raises(RequestAborted):
            self._post(r)

    def test_json_without_post_id_aborts(self):
        r = _http_response(200, b'{"error": "unexpected"}')
        with pytest.raises(RequestAborted):
            self._post(r)


@pytest.mark.django_db(databases="__all__")
class TestMastodonApplicationRegistration:
    def test_existing_api_domain_is_reused_with_whitelist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = MastodonApplication.objects.create(
            domain_name="mastodon.online",
            api_domain="api.mastodon.online",
            server_version="4.5.0",
            client_id="existing",
            client_secret="secret",
        )
        monkeypatch.setattr(
            SiteConfig.system, "mastodon_login_whitelist", ["allowed.example"]
        )
        with (
            patch("mastodon.models.mastodon.get") as get,
            patch("mastodon.models.mastodon.post") as post,
        ):
            result = get_or_create_fediverse_application("API.MASTODON.ONLINE")
        assert result.pk == app.pk
        get.assert_not_called()
        post.assert_not_called()

    def test_new_application_is_registered_and_verified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SiteConfig.system, "mastodon_login_whitelist", [])
        metadata = {"uri": "mastodon.online", "version": "4.5.0"}
        credentials = {"id": "4", "client_id": "created", "client_secret": "secret"}
        with (
            patch("mastodon.models.mastodon.is_valid_url", return_value=True),
            patch(
                "mastodon.models.mastodon.get",
                return_value=_http_response(200, json.dumps(metadata).encode()),
            ),
            patch(
                "mastodon.models.mastodon.post",
                side_effect=[
                    _http_response(200, json.dumps(credentials).encode()),
                    _http_response(200, b'{"access_token": "client-token"}'),
                ],
            ) as post,
        ):
            app = get_or_create_fediverse_application("mastodon.online")
        app.refresh_from_db()
        assert app.domain_name == "mastodon.online"
        assert app.api_domain == "mastodon.online"
        assert app.client_id == "created"
        assert app.client_secret == "secret"
        assert app.server_version == "4.5.0"
        assert post.call_args_list[0].args == ("https://mastodon.online/api/v1/apps",)
        token_call = post.call_args_list[1]
        assert token_call.args == ("https://mastodon.online/oauth/token",)
        assert token_call.kwargs["data"]["grant_type"] == "client_credentials"

    @pytest.mark.parametrize("reason", ["whitelist", "local"])
    def test_disallowed_instance_is_rejected_before_network(
        self, monkeypatch: pytest.MonkeyPatch, reason: str
    ) -> None:
        domain = "mastodon.online" if reason == "whitelist" else settings.SITE_DOMAIN
        whitelist = ["allowed.example"] if reason == "whitelist" else []
        monkeypatch.setattr(SiteConfig.system, "mastodon_login_whitelist", whitelist)
        with (
            patch("mastodon.models.mastodon.get") as get,
            patch("mastodon.models.mastodon.post") as post,
        ):
            with pytest.raises(ValueError, match="Unsupported instance"):
                get_or_create_fediverse_application(domain)
        get.assert_not_called()
        post.assert_not_called()

    @pytest.mark.parametrize("failure", ["http", "json"])
    def test_failed_registration_does_not_persist_application(
        self, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        monkeypatch.setattr(SiteConfig.system, "mastodon_login_whitelist", [])
        response = (
            _http_response(400, b'{"error_description": "invalid redirect"}')
            if failure == "http"
            else _http_response(200, b"not JSON")
        )
        with (
            patch("mastodon.models.mastodon.is_valid_url", return_value=True),
            patch(
                "mastodon.models.mastodon.get",
                return_value=_http_response(
                    200, b'{"uri":"mastodon.online","version":"4.5.0"}'
                ),
            ),
            patch("mastodon.models.mastodon.post", return_value=response),
        ):
            with pytest.raises(Exception, match="Error creating app"):
                get_or_create_fediverse_application("mastodon.online")
        assert not MastodonApplication.objects.filter(
            domain_name="mastodon.online"
        ).exists()

    def test_auth_url_uses_api_domain_and_saves_callback_state(self) -> None:
        app = MastodonApplication.objects.create(
            domain_name="mastodon.online",
            api_domain="api.mastodon.online",
            server_version="4.5.0",
            client_id="login-client",
            client_secret="secret",
        )
        request = RequestFactory().get("/", secure=True, HTTP_HOST=settings.SITE_DOMAIN)
        request.session = SessionStore()
        with patch("mastodon.models.mastodon.get") as get:
            url = Mastodon.generate_auth_url(" https://MASTODON.ONLINE/@user ", request)
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        assert parsed.netloc == app.api_domain
        assert parsed.path == "/oauth/authorize"
        assert query["client_id"] == [app.client_id]
        assert query["response_type"] == ["code"]
        assert query["redirect_uri"] == [
            settings.SITE_INFO["site_url"] + "/account/login/oauth"
        ]
        assert query["state"] == [request.session["mastodon_oauth_state"]]
        assert request.session["mastodon_oauth_state"]
        assert request.session["mastodon_domain"] == app.domain_name
        get.assert_not_called()


class TestMastodonInstanceProbe:
    def test_private_domain_never_probes(self) -> None:
        with (
            patch("mastodon.models.mastodon.is_valid_url", return_value=False),
            patch("mastodon.models.mastodon.get") as get,
        ):
            with pytest.raises(Exception, match="Invalid instance domain"):
                detect_server_info("127.0.0.1")
        get.assert_not_called()

    @pytest.mark.parametrize("failure", ["network", "http", "json"])
    def test_unusable_metadata_is_rejected(self, failure: str) -> None:
        with (
            patch("mastodon.models.mastodon.is_valid_url", return_value=True),
            patch("mastodon.models.mastodon.get") as get,
        ):
            if failure == "network":
                get.side_effect = requests.Timeout("probe timeout")
            elif failure == "http":
                get.return_value = _http_response(503, b"unavailable")
            else:
                get.return_value = _http_response(200, b"not JSON")
            with pytest.raises(Exception, match="instance|Instance"):
                detect_server_info("mastodon.online")

    def test_separate_account_domain_keeps_working_api_domain(self) -> None:
        with (
            patch("mastodon.models.mastodon.is_valid_url", return_value=True),
            patch(
                "mastodon.models.mastodon.get",
                side_effect=[
                    _http_response(200, b'{"uri":"mastodon.online","version":"4.5.0"}'),
                    requests.Timeout("account domain has no API"),
                ],
            ),
        ):
            assert detect_server_info("api.mastodon.online") == (
                "mastodon.online",
                "api.mastodon.online",
                "4.5.0",
            )
