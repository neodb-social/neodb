from unittest.mock import Mock, patch

import pytest
from django.conf import settings

from mastodon.models import MastodonAccount, MastodonApplication, Platform
from mastodon.models.mastodon import (
    TootVisibilityEnum,
    _force_recreate_app,
    _get_redirect_uris,
    _get_scopes,
    get_toot_visibility,
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
    def test_returns_string(self):
        result = _get_redirect_uris("4.1.0")
        assert isinstance(result, str)
        assert len(result) > 0

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

    def _detect(self, app: MastodonApplication, emoji_response: Mock) -> None:
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
