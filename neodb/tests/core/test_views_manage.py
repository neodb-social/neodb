from typing import Any

import pydantic
import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from boofilsic import settings as boofilsic_settings
from common.config import resolve_email_settings
from common.models import SiteConfig
from common.views_manage import (
    AccessSettings,
    AdvancedSettings,
    APIKeysSettings,
    BrandingSettings,
    CatalogSettings,
    DiscoverSettings,
    DownloaderSettings,
    FederationSettings,
    RecommendationSettings,
)

ALL_SETTINGS_PAGES = [
    BrandingSettings,
    DiscoverSettings,
    RecommendationSettings,
    AccessSettings,
    FederationSettings,
    CatalogSettings,
    APIKeysSettings,
    DownloaderSettings,
    AdvancedSettings,
]


class TestSettingsCoverage:
    """Ensure every SystemOptions field appears in exactly one settings page."""

    def test_all_system_options_in_ui(self):
        all_model_fields = set(SiteConfig.SystemOptions.model_fields.keys())
        ui_fields_list: list[str] = []
        for page_cls in ALL_SETTINGS_PAGES:
            ui_fields_list.extend(page_cls.options.keys())

        ui_fields_set = set(ui_fields_list)

        duplicates = [f for f in ui_fields_set if ui_fields_list.count(f) > 1]
        assert not duplicates, (
            f"Fields appearing in multiple settings pages: {duplicates}"
        )

        missing = all_model_fields - ui_fields_set
        assert not missing, (
            f"SystemOptions fields missing from settings UI: {missing}. "
            f"Add them to a SiteConfigSettingsPage subclass."
        )

    def test_no_unknown_fields_in_ui(self):
        all_model_fields = set(SiteConfig.SystemOptions.model_fields.keys())
        ui_fields: set[str] = set()
        for page_cls in ALL_SETTINGS_PAGES:
            ui_fields.update(page_cls.options.keys())
        extra = ui_fields - all_model_fields
        assert not extra, f"Settings UI references fields not in SystemOptions: {extra}"

    def test_layout_matches_options(self):
        for page_cls in ALL_SETTINGS_PAGES:
            layout_fields_list: list[str] = []
            for fields in page_cls.layout.values():
                layout_fields_list.extend(fields)

            layout_fields = set(layout_fields_list)
            options_fields = set(page_cls.options.keys())

            duplicates = [f for f in layout_fields if layout_fields_list.count(f) > 1]
            assert not duplicates, (
                f"{page_cls.__name__}.layout has duplicate fields: {duplicates}"
            )

            missing = options_fields - layout_fields
            assert not missing, (
                f"{page_cls.__name__}.layout is missing fields from options: {missing}"
            )
            extra = layout_fields - options_fields
            assert not extra, (
                f"{page_cls.__name__}.layout has fields not in options: {extra}"
            )


class TestEnvironmentOnlySettings:
    def test_site_name_has_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NEODB_SITE_NAME", raising=False)
        monkeypatch.delenv("NEODB_SITE_NAME_FILE", raising=False)

        assert boofilsic_settings.env("NEODB_SITE_NAME") == "My NeoDB Site"

    @pytest.mark.django_db(databases="__all__")
    def test_legacy_sentry_data_is_ignored(self) -> None:
        legacy_data = {
            "sentry_dsn": "https://example.invalid/1",
            "sentry_sample_rate": 0.5,
        }
        SiteConfig.objects.update_or_create(pk=1, defaults={"data": legacy_data})

        site_config = SiteConfig.load_system()

        assert "sentry_dsn" not in SiteConfig.SystemOptions.model_fields
        assert "sentry_sample_rate" not in SiteConfig.SystemOptions.model_fields
        assert SiteConfig.objects.get(pk=1).data == legacy_data
        assert not hasattr(site_config, "sentry_dsn")
        assert not hasattr(site_config, "sentry_sample_rate")


@pytest.mark.django_db(databases="__all__")
class TestMastodonLoginSettings:
    def test_enabled_by_default(self) -> None:
        assert SiteConfig.SystemOptions().enable_login_mastodon is True

    def test_database_can_disable_login(self) -> None:
        old_system = getattr(SiteConfig, "system", None)
        try:
            SiteConfig.set_system(enable_login_mastodon=False)

            SiteConfig.reload()

            assert SiteConfig.system.enable_login_mastodon is False
            assert SiteConfig.objects.get(pk=1).data["enable_login_mastodon"] is False
        finally:
            SiteConfig.objects.filter(pk=1).delete()
            if old_system is not None:
                SiteConfig.system = old_system
                SiteConfig._apply_to_settings(old_system)


class TestResolveEmailSettings:
    def test_smtp_tls_url(self) -> None:
        config = resolve_email_settings(
            "smtp+tls://user:password@smtp.example.org:587", False
        )

        assert config["EMAIL_BACKEND"] == (
            "django.core.mail.backends.smtp.EmailBackend"
        )
        assert config["EMAIL_HOST"] == "smtp.example.org"
        assert config["EMAIL_PORT"] == 587
        assert config["EMAIL_USE_TLS"] is True
        assert config["ENABLE_LOGIN_EMAIL"] is True

    def test_anymail_url(self) -> None:
        config = resolve_email_settings("anymail://mailgun?API_KEY=secret", True)

        assert config["EMAIL_BACKEND"] == "anymail.backends.mailgun.EmailBackend"
        assert config["ANYMAIL"] == {
            "API_KEY": "secret",
            "DEBUG_API_REQUESTS": True,
        }
        assert config["ENABLE_LOGIN_EMAIL"] is True

    def test_anymail_url_without_debug(self) -> None:
        config = resolve_email_settings("anymail://mailgun?API_KEY=secret", False)

        assert config["ANYMAIL"] == {"API_KEY": "secret"}

    def test_console_url_in_debug(self) -> None:
        config = resolve_email_settings("console://", True)

        assert config["EMAIL_BACKEND"] == (
            "django.core.mail.backends.console.EmailBackend"
        )
        assert config["ENABLE_LOGIN_EMAIL"] is True

    @pytest.mark.parametrize("email_url", [None, "", 123])
    def test_invalid_or_missing_url_type_disables_email(
        self, email_url: object
    ) -> None:
        config = resolve_email_settings(email_url, False)

        assert config["EMAIL_BACKEND"] == (
            "django.core.mail.backends.dummy.EmailBackend"
        )
        assert config["ENABLE_LOGIN_EMAIL"] is False

    def test_url_without_scheme_disables_email(self) -> None:
        config = resolve_email_settings("smtp.example.org", False)

        assert config["EMAIL_BACKEND"] == (
            "django.core.mail.backends.dummy.EmailBackend"
        )
        assert config["ENABLE_LOGIN_EMAIL"] is False

    def test_invalid_url_scheme(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="Invalid email schema"):
            resolve_email_settings("invalid://example.org", False)

    @pytest.mark.parametrize(
        ("email_url", "error"),
        [
            ("anymail://", "Anymail URL must include a backend name"),
            ("smtp://", "SMTP URL must include a host"),
        ],
    )
    def test_missing_backend_or_host(self, email_url: str, error: str) -> None:
        with pytest.raises(ImproperlyConfigured, match=error):
            resolve_email_settings(email_url, False)

    def test_invalid_url_is_rejected_by_site_config(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="Invalid email schema"):
            SiteConfig.SystemOptions(email_url="invalid://example.org")

    def test_none_environment_values_use_empty_defaults(self, settings: Any) -> None:
        settings.EMAIL_URL_ENV = None
        settings.DEFAULT_FROM_EMAIL_ENV = None

        defaults = SiteConfig._env_defaults()

        assert defaults["email_url"] == ""
        assert defaults["email_from"] == ""


@pytest.mark.django_db(databases="__all__")
class TestEmailSettingsApply:
    @pytest.mark.parametrize(
        ("db_url", "expected_backend", "enabled"),
        [
            ("memorymail://", "django.core.mail.backends.locmem.EmailBackend", True),
            ("", "django.core.mail.backends.dummy.EmailBackend", False),
        ],
    )
    def test_db_value_overrides_environment_fallback(
        self,
        settings: Any,
        db_url: str,
        expected_backend: str,
        enabled: bool,
    ) -> None:
        settings.EMAIL_URL_ENV = "smtp://env-user:env-pass@smtp.example.org:25"
        settings.DEFAULT_FROM_EMAIL_ENV = "Environment <env@example.org>"
        old_system = getattr(SiteConfig, "system", None)
        try:
            SiteConfig.set_system(
                email_url=db_url,
                email_from="NeoDB Test <test@example.org>",
            )

            SiteConfig.reload()

            assert SiteConfig.system.email_url == db_url
            assert settings.EMAIL_URL == db_url
            assert settings.EMAIL_BACKEND == expected_backend
            assert settings.DEFAULT_FROM_EMAIL == "NeoDB Test <test@example.org>"
            assert settings.ENABLE_LOGIN_EMAIL is enabled
            assert settings.SITE_INFO["enable_login_email"] is enabled
        finally:
            SiteConfig.objects.filter(pk=1).delete()
            if old_system is not None:
                SiteConfig.system = old_system
                SiteConfig._apply_to_settings(old_system)


class TestConvertValueList:
    """Test _convert_value for list-type fields."""

    @pytest.fixture(autouse=True)
    def setup_view(self):
        self.view = AccessSettings()

    def test_list_from_multiline_string(self):
        result = self.view._convert_value(
            "mastodon_login_whitelist", "example.com\nother.org\n"
        )
        assert result == ["example.com", "other.org"]

    def test_list_empty_string(self):
        result = self.view._convert_value("mastodon_login_whitelist", "")
        assert result == []

    def test_list_none_value(self):
        result = self.view._convert_value("mastodon_login_whitelist", None)
        assert result == []

    def test_list_strips_blank_lines(self):
        result = self.view._convert_value("mastodon_login_whitelist", "a\n\n  \nb\n")
        assert result == ["a", "b"]


class TestConvertValueDict:
    """Test _convert_value for dict-type fields."""

    @pytest.fixture(autouse=True)
    def setup_view(self):
        self.view = BrandingSettings()

    def test_dict_with_json_schema_returns_raw(self):
        raw = {"key": "value"}
        result = self.view._convert_value("site_links", raw)
        assert result == {"key": "value"}

    def test_dict_with_json_schema_none_returns_empty(self):
        result = self.view._convert_value("site_links", None)
        assert result == {}


class TestConvertValueSimple:
    """Test _convert_value for simple types (str, bool, int)."""

    @pytest.fixture(autouse=True)
    def setup_view(self):
        self.branding = BrandingSettings()
        self.access = AccessSettings()
        self.discover = DiscoverSettings()

    def test_passthrough_for_string(self):
        result = self.branding._convert_value("site_name", "My Site")
        assert result == "My Site"

    def test_passthrough_bool(self):
        result = self.access._convert_value("invite_only", True)
        assert result is True

    def test_passthrough_int(self):
        result = self.discover._convert_value("min_marks_for_discover", 5)
        assert result == 5


@pytest.mark.django_db(databases="__all__")
class TestMastodonTimeoutApply:
    """DB-stored mastodon_timeout must reach django settings on reload."""

    def test_db_value_applies_to_settings(self):
        old_mastodon = settings.MASTODON_TIMEOUT
        old_takahe = settings.TAKAHE_REMOTE_TIMEOUT
        old_system = getattr(SiteConfig, "system", None)
        try:
            SiteConfig.set_system(mastodon_timeout=17)
            SiteConfig.reload()
            assert SiteConfig.system.mastodon_timeout == 17
            assert settings.MASTODON_TIMEOUT == 17
            assert settings.TAKAHE_REMOTE_TIMEOUT == 17
        finally:
            settings.MASTODON_TIMEOUT = old_mastodon
            settings.TAKAHE_REMOTE_TIMEOUT = old_takahe
            if old_system is not None:
                SiteConfig.system = old_system
