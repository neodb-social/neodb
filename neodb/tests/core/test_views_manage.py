import inspect
import re
from pathlib import Path
from typing import Any

import pydantic
import pytest
from django import forms
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import translation
from django.utils.translation import trans_real

from boofilsic import settings as boofilsic_settings
from common.config import hide_secret, resolve_email_settings
from common.models import SiteConfig
from users.middlewares import activate_language_for_user
from users.models import User
from common.views_manage import (
    ENV_VARS_WITH_SITE_SETTING,
    AccessSettings,
    AdvancedSettings,
    APIKeysSettings,
    BrandingSettings,
    CatalogSettings,
    DiscoverSettings,
    DownloaderSettings,
    EnvironmentSettings,
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
            assert settings.ENABLE_LOGIN_EMAIL is enabled
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
    """The Mastodon client sends the DB-stored mastodon_timeout after a reload."""

    def test_db_value_is_used_by_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mastodon.models import mastodon as mastodon_module

        seen: dict[str, Any] = {}

        def fake_request(method: str, url: str, **kwargs: Any) -> None:
            seen.update(kwargs)

        monkeypatch.setattr(mastodon_module.requests, "request", fake_request)
        old_system = getattr(SiteConfig, "system", None)
        try:
            SiteConfig.set_system(mastodon_timeout=17)
            SiteConfig.reload()

            mastodon_module.get("https://mastodon.example/api/v1/instance")

            assert SiteConfig.system.mastodon_timeout == 17
            assert seen["timeout"] == 17
        finally:
            SiteConfig.objects.filter(pk=1).delete()
            if old_system is not None:
                SiteConfig.system = old_system


class TestLanguageCodeOptions:
    def test_default_is_english(self) -> None:
        assert SiteConfig.SystemOptions().language_code == "en"

    def test_environment_value_is_the_fallback(self, settings: Any) -> None:
        settings.LANGUAGE_CODE_ENV = "zh-hant"

        assert SiteConfig._env_defaults()["language_code"] == "zh-hant"

    def test_environment_subtag_is_not_flattened_by_preferred_languages(
        self, settings: Any
    ) -> None:
        """preferred_languages collapses zh-hant to zh; language_code must not."""
        settings.LANGUAGE_CODE_ENV = "zh-hant"
        settings.PREFERRED_LANGUAGES = ["zh", "en"]

        defaults = SiteConfig._env_defaults()

        assert defaults["language_code"] == "zh-hant"
        assert defaults["preferred_languages"] == ["zh", "en"]

    def test_missing_environment_value_falls_back_to_english(
        self, settings: Any
    ) -> None:
        settings.LANGUAGE_CODE_ENV = None

        assert SiteConfig._env_defaults()["language_code"] == "en"

    def test_unsupported_code_is_rejected(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="not a supported"):
            SiteConfig.SystemOptions(language_code="tlh")

    def test_supported_code_is_accepted(self) -> None:
        assert SiteConfig.SystemOptions(language_code="zh-hant").language_code

    def test_offered_choices_are_supported_ui_languages(self) -> None:
        choices = AccessSettings.options["language_code"]["choices"]

        assert [code for code, _label in choices] == list(
            settings.SUPPORTED_UI_LANGUAGES
        )

    def test_renders_as_a_select(self) -> None:
        form = AccessSettings().get_form_class()()

        widget = form.fields["language_code"].widget

        assert isinstance(widget, forms.Select)
        assert "zh-hans" in [code for code, _label in widget.choices]


@pytest.mark.django_db(databases="__all__")
class TestLanguageCodeApply:
    """DB-stored language_code must reach settings.LANGUAGE_CODE on reload."""

    def test_db_value_overrides_environment_fallback(self, settings: Any) -> None:
        settings.LANGUAGE_CODE_ENV = "en"
        old_language = settings.LANGUAGE_CODE
        old_system = getattr(SiteConfig, "system", None)
        try:
            SiteConfig.set_system(language_code="zh-hans")

            SiteConfig.reload()

            assert SiteConfig.system.language_code == "zh-hans"
            assert settings.LANGUAGE_CODE == "zh-hans"
            # No catalog for the old language may survive. It is dropped on
            # change, and anything that forces a lazy string afterwards (the
            # cache refresh does) rebuilds it for the new language.
            cached = trans_real._default  # ty: ignore[unresolved-attribute]
            assert cached is None or cached.language() == "zh-hans"
        finally:
            SiteConfig.objects.filter(pk=1).delete()
            settings.LANGUAGE_CODE = old_language
            trans_real._default = None  # ty: ignore[unresolved-attribute]
            if old_system is not None:
                SiteConfig.system = old_system
                SiteConfig._apply_to_settings(old_system)

    def test_applies_to_visitors_without_a_preference(self, settings: Any) -> None:
        settings.LANGUAGE_CODE_ENV = "en"
        old_language = settings.LANGUAGE_CODE
        old_system = getattr(SiteConfig, "system", None)
        try:
            SiteConfig.set_system(language_code="zh-hans")
            SiteConfig.reload()

            activate_language_for_user(None)

            assert translation.get_language() == "zh-hans"
        finally:
            translation.deactivate()
            SiteConfig.objects.filter(pk=1).delete()
            settings.LANGUAGE_CODE = old_language
            trans_real._default = None  # ty: ignore[unresolved-attribute]
            if old_system is not None:
                SiteConfig.system = old_system
                SiteConfig._apply_to_settings(old_system)

    def test_unchanged_value_leaves_cached_translation_alone(
        self, settings: Any
    ) -> None:
        """The 30s config refresh must not clear the translation cache."""
        settings.LANGUAGE_CODE_ENV = settings.LANGUAGE_CODE
        translation.activate(settings.LANGUAGE_CODE)
        trans_real._default = trans_real.translation(  # ty: ignore[unresolved-attribute]
            settings.LANGUAGE_CODE
        )
        try:
            SiteConfig._apply_to_settings(SiteConfig.load_system())

            assert trans_real._default is not None  # ty: ignore[unresolved-attribute]
        finally:
            translation.deactivate()
            trans_real._default = None  # ty: ignore[unresolved-attribute]


@pytest.mark.django_db(databases="__all__")
class TestEnvDefaultsIsolation:
    """Env fallbacks must not follow the DB values applied to django settings.

    set_system() drops any value equal to its env fallback. If the fallbacks
    tracked the effective values instead, re-saving a settings page would
    delete the stored overrides, which then vanish on the next restart (#1828).
    """

    @staticmethod
    def _changed_options() -> SiteConfig.SystemOptions:
        """Every field set to a value that differs from the current config."""
        current = SiteConfig.load_system()
        values: dict[str, Any] = {}
        for field in SiteConfig.SystemOptions.model_fields:
            value = getattr(current, field)
            if field == "email_url":
                values[field] = "smtp://user:pw@mail.example.org:587"
            elif field == "language_code":
                values[field] = next(
                    code for code in settings.SUPPORTED_UI_LANGUAGES if code != value
                )
            elif field == "preferred_languages":
                values[field] = [
                    *value,
                    next(c for c in ("fr", "de", "ja", "ko") if c not in value),
                ]
            elif isinstance(value, bool):
                values[field] = not value
            elif isinstance(value, int | float):
                values[field] = value + 1
            elif isinstance(value, str):
                values[field] = f"{value}-changed"
            elif isinstance(value, list):
                values[field] = [*value, "changed"]
            elif isinstance(value, dict):
                values[field] = {**value, "changed": "changed"}
            else:
                raise AssertionError(f"unhandled type for {field}: {type(value)}")
        # validators would reject some perturbed values and are irrelevant here
        return SiteConfig.SystemOptions.model_construct(**values)

    def test_apply_to_settings_does_not_change_env_defaults(self) -> None:
        # Exhaustive on purpose: a new `settings.X = opts.x` line in
        # _apply_to_settings for an attribute _env_defaults reads would bring
        # the bug back for that field, so every field is perturbed.
        old_system = getattr(SiteConfig, "system", None)
        before = SiteConfig._env_defaults()
        try:
            SiteConfig._apply_to_settings(self._changed_options())

            assert SiteConfig._env_defaults() == before
        finally:
            if old_system is not None:
                SiteConfig.system = old_system
                SiteConfig._apply_to_settings(old_system)
            else:
                SiteConfig.reload()

    def test_resaving_branding_keeps_stored_values(self) -> None:
        old_system = getattr(SiteConfig, "system", None)
        try:
            SiteConfig.set_system(site_name="Changed Name", mastodon_timeout=99)
            SiteConfig.reload()
            # The form posts every field of the page, unchanged ones included.
            SiteConfig.set_system(
                site_name="Changed Name", site_color="red", mastodon_timeout=99
            )

            data = SiteConfig.objects.get(pk=1).data
            assert data["site_name"] == "Changed Name"
            assert data["site_color"] == "red"
            assert data["mastodon_timeout"] == 99
        finally:
            SiteConfig.objects.filter(pk=1).delete()
            if old_system is not None:
                SiteConfig.system = old_system
                SiteConfig._apply_to_settings(old_system)
            else:
                SiteConfig.reload()


class TestSiteConfigReaders:
    """Application code reads SiteConfig fields through SiteConfig.system.

    A Django setting that only seeds a SiteConfig field holds the .env value,
    so reading it directly ignores the value set in the UI. Settings that
    _apply_to_settings() keeps in sync (Django consumes those) are exempt.
    """

    # (path relative to the neodb package, setting): documented exceptions
    ALLOWED = {
        # initial value of a module-level list that _apply_to_settings rewrites
        ("common/models/lang.py", "PREFERRED_LANGUAGES"),
    }
    SKIP_PATHS = ("tests/", "boofilsic/settings.py", "common/models/site_config.py")

    def test_no_direct_reads_of_seed_settings(self) -> None:
        seeds = inspect.getsource(SiteConfig._env_defaults)
        synced = set(
            re.findall(
                r"settings\.(\w+)\s*=", inspect.getsource(SiteConfig._apply_to_settings)
            )
        )
        patterns = {
            name: rf"\bsettings\.{name}\b"
            for name in set(re.findall(r'getattr\(\s*settings,\s*"(\w+)"', seeds))
            - synced
        }
        patterns |= {
            f"SITE_INFO[{key}]": rf'settings\.SITE_INFO(\["{key}"\]|\.get\(\s*"{key}")'
            for key in re.findall(r'site_info\.get\(\s*"(\w+)"', seeds)
        }

        root = Path(boofilsic_settings.__file__).resolve().parent.parent
        offenders = []
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(self.SKIP_PATHS) or "/migrations/" in rel:
                continue
            text = path.read_text()
            for name, pattern in patterns.items():
                if (rel, name) not in self.ALLOWED and re.search(pattern, text):
                    offenders.append(f"{rel}: settings.{name}")

        assert not offenders, (
            "Read these through SiteConfig.system instead, or add them to"
            f" TestSiteConfigReaders.ALLOWED with a reason: {offenders}"
        )


class TestEnvironmentCoverage:
    """Every env var read by boofilsic.settings is visible on some manage page."""

    @staticmethod
    def _env_vars_read_by_settings() -> set[str]:
        source = Path(boofilsic_settings.__file__).read_text()
        names = set(re.findall(r'\benv(?:\.\w+)?\(\s*"([A-Z0-9_]+)"', source))
        return names | set(boofilsic_settings.env.scheme)

    def test_every_env_var_is_covered(self) -> None:
        env_vars = self._env_vars_read_by_settings()
        shown = EnvironmentSettings.env_var_names()
        seeded = set(ENV_VARS_WITH_SITE_SETTING)

        assert not shown & seeded, f"Env vars listed twice: {shown & seeded}"
        missing = env_vars - shown - seeded
        assert not missing, (
            f"Env vars read by settings but not visible in the manage UI: {missing}."
            " Add them to EnvironmentSettings.groups, or to"
            " ENV_VARS_WITH_SITE_SETTING if a SiteConfig field shows them."
        )
        stale = (shown | seeded) - env_vars
        assert not stale, f"Manage UI lists env vars that settings never reads: {stale}"

    def test_seeded_fields_exist(self) -> None:
        fields = set(SiteConfig.SystemOptions.model_fields)
        stale = {k: v for k, v in ENV_VARS_WITH_SITE_SETTING.items() if v not in fields}
        assert not stale, (
            f"ENV_VARS_WITH_SITE_SETTING points at unknown fields: {stale}"
        )


class TestHideSecret:
    @pytest.mark.parametrize(
        ("name", "value", "expected"),
        [
            ("NEODB_SECRET_KEY", "abc", "********"),
            ("TAKAHE_STATOR_TOKEN", "abc", "********"),
            ("NEODB_SECRET_KEY", "", ""),
            (
                "NEODB_DB_URL",
                "postgres://neodb:pw@db:5432/neodb",
                "postgres://neodb:********@db:5432/neodb",
            ),
            ("NEODB_REDIS_URL", "redis://redis:6379/0", "redis://redis:6379/0"),
            (
                "NEODB_SENTRY_DSN",
                "https://k3y@o1.ingest.sentry.io/2",
                "https://********@o1.ingest.sentry.io/2",
            ),
            ("NEODB_SITE_DOMAIN", "example.org", "example.org"),
            ("NEODB_DEBUG", True, "True"),
            ("NEODB_ADMIN_HANDLES", ["a", "b"], "a, b"),
            ("NEODB_EXTRA_APPS", None, ""),
        ],
    )
    def test_hide(self, name: str, value: object, expected: str) -> None:
        assert hide_secret(name, value) == expected


@pytest.mark.django_db(databases="__all__")
class TestEnvironmentPage:
    @staticmethod
    def _login(client: Any, superuser: bool) -> None:
        user = User.register(username="admin" if superuser else "bob")
        if superuser:
            user.is_superuser = True
            user.save(update_fields=["is_superuser"])
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    def test_shows_settings_with_secrets_masked(
        self, client: Any, monkeypatch: pytest.MonkeyPatch, settings: Any
    ) -> None:
        settings.SECRET_KEY = "top-secret-key-value"
        monkeypatch.setenv("TAKAHE_STATOR_TOKEN", "stator-secret-token")
        monkeypatch.setenv("TAKAHE_MAIN_DOMAIN", "fedi.example.org")
        self._login(client, superuser=True)

        response = client.get("/manage/environment/")

        assert response.status_code == 200
        html = response.content.decode()
        assert "NEODB_SITE_DOMAIN" in html
        assert settings.SITE_DOMAIN in html
        assert "NEODB_SECRET_KEY" in html
        assert "top-secret-key-value" not in html
        # variables this process has that settings does not read are listed raw
        assert "TAKAHE_MAIN_DOMAIN" in html
        assert "fedi.example.org" in html
        assert "TAKAHE_STATOR_TOKEN" in html
        assert "stator-secret-token" not in html
        # the raw connection strings are shown, with the password masked
        assert hide_secret("NEODB_DB_URL", settings.DB_URL) in html
        assert hide_secret("TAKAHE_DB_URL", settings.TAKAHE_DB_URL) in html
        for alias in ("default", "takahe"):
            password = settings.DATABASES[alias].get("PASSWORD")
            if password:
                assert password not in html

    def test_requires_superuser(self, client: Any) -> None:
        self._login(client, superuser=False)

        response = client.get("/manage/environment/")

        assert response.status_code == 302

    def test_form_pages_render_through_shared_base(self, client: Any) -> None:
        # Branding has a JSONFormField, so this also covers form.media in base
        self._login(client, superuser=True)

        response = client.get("/manage/branding/")

        assert response.status_code == 200
        html = response.content.decode()
        assert 'class="manage-form"' in html
        assert 'type="submit"' in html
        assert "/manage/environment/" in html
