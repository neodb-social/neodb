from typing import Any

import pytest
from django.conf import settings
from django.utils import translation

import catalog.models.common as catalog_common
import catalog.sites.tmdb as tmdb
import common.models.lang as lang
from catalog.common.downloaders import BasicDownloader
from common.models.lang import (
    get_current_locales,
    localize_number,
    localized_label_text,
)


class TestLocalizeNumber:
    def test_chinese_single_digit(self):
        with translation.override("zh-hans"):
            assert localize_number(0) == "\u96f6"  # zero
            assert localize_number(1) == "\u4e00"  # one
            assert localize_number(5) == "\u4e94"  # five
            assert localize_number(9) == "\u4e5d"  # nine

    def test_chinese_teens(self):
        with translation.override("zh-hans"):
            assert localize_number(10) == "\u5341"  # ten
            assert localize_number(11) == "\u5341\u4e00"  # ten + one
            assert localize_number(19) == "\u5341\u4e5d"  # ten + nine

    def test_chinese_double_digit(self):
        with translation.override("zh-hans"):
            result = localize_number(25)
            assert "\u5341" in result  # should contain "ten"

    def test_chinese_out_of_range_negative(self):
        with translation.override("zh-hans"):
            assert localize_number(-1) == "-1"

    def test_chinese_out_of_range_large(self):
        with translation.override("zh-hans"):
            assert localize_number(100) == "100"

    def test_chinese_hant(self):
        with translation.override("zh-hant"):
            assert localize_number(3) == "\u4e09"  # three

    def test_non_chinese_returns_str(self):
        with translation.override("en"):
            assert localize_number(42) == "42"

    def test_french_returns_str(self):
        with translation.override("fr"):
            assert localize_number(7) == "7"


class TestGetCurrentLocales:
    def test_zh_hans_locale(self):
        with translation.override("zh-hans"):
            locales = get_current_locales()
            assert locales[0] == "zh-cn"
            assert "en" in locales

    def test_zh_hant_locale(self):
        with translation.override("zh-hant"):
            locales = get_current_locales()
            assert locales[0] == "zh-tw"
            assert "en" in locales

    def test_en_locale(self):
        with translation.override("en"):
            locales = get_current_locales()
            assert locales[0] == "en"

    def test_other_locale(self):
        with translation.override("fr"):
            locales = get_current_locales()
            assert locales[0] == "fr"
            assert "en" in locales


class TestPreferredLanguagesIsolation:
    def test_runtime_cache_does_not_alias_the_setting(self):
        """SiteConfig rewrites SITE_PREFERRED_LANGUAGES in place and reads
        settings.PREFERRED_LANGUAGES back as the env fallback, so the two must
        not be the same list."""
        assert lang.SITE_PREFERRED_LANGUAGES is not settings.PREFERRED_LANGUAGES

    def test_rewriting_the_cache_leaves_the_setting_alone(self):
        original_setting = list(settings.PREFERRED_LANGUAGES)
        original_cache = list(lang.SITE_PREFERRED_LANGUAGES)
        try:
            lang.SITE_PREFERRED_LANGUAGES[:] = ["ja", "en"]

            assert list(settings.PREFERRED_LANGUAGES) == original_setting
        finally:
            lang.SITE_PREFERRED_LANGUAGES[:] = original_cache


class TestLanguageConsumersFollowSettings:
    """settings.LANGUAGE_CODE is runtime-configurable, so values derived from it
    must not stay pinned to whatever was in the environment at startup."""

    def test_tmdb_keeps_no_import_time_language_cache(self) -> None:
        """These were module constants, so TMDB requests kept the startup
        language for the life of the process. Keep them per-call."""
        assert not hasattr(tmdb, "TMDB_DEFAULT_LANG")
        assert not hasattr(tmdb, "TMDB_PREFERRED_LANGS")

    def test_tmdb_language_is_resolved_per_call(self, settings: Any) -> None:
        settings.LANGUAGE_CODE = "zh-hant"

        assert tmdb._get_language_code() == "zh-TW"

    def test_tmdb_preferred_languages_are_resolved_per_call(self) -> None:
        original = list(lang.SITE_PREFERRED_LANGUAGES)
        try:
            lang.SITE_PREFERRED_LANGUAGES[:] = ["ja", "en"]

            assert list(tmdb._get_preferred_languages()) == ["ja", "en"]
        finally:
            lang.SITE_PREFERRED_LANGUAGES[:] = original

    def test_downloader_accept_language_follows_a_refresh(self, settings: Any) -> None:
        original = BasicDownloader.headers["Accept-Language"]
        try:
            settings.LANGUAGE_CODE = "zh-hans"

            lang.refresh_language_caches()

            assert BasicDownloader.headers["Accept-Language"].startswith("zh-CN")
            # callers that spread the class attribute see it too
            assert {**BasicDownloader.headers}["Accept-Language"].startswith("zh-CN")
        finally:
            BasicDownloader.headers["Accept-Language"] = original


class TestLanguageCacheRefresh:
    """The choice caches are ordered by SITE_PREFERRED_LANGUAGES, which is only
    known from env at import time. A later SiteConfig change must reach them."""

    @pytest.fixture(autouse=True)
    def restore_caches(self):
        original = list(lang.SITE_PREFERRED_LANGUAGES)
        yield
        lang.SITE_PREFERRED_LANGUAGES[:] = original
        lang.refresh_language_caches()

    def test_preferred_languages_lead_the_choices(self):
        lang.SITE_PREFERRED_LANGUAGES[:] = ["ja", "en"]

        lang.refresh_language_caches()

        assert lang.LANGUAGE_CHOICES[0][0] == "ja"
        assert lang.LOCALE_CHOICES[0][0] == "ja"
        assert lang.SCRIPT_CHOICES[0][0] == "ja"
        assert next(iter(lang.LANGUAGE_CODES)) == "ja"
        # every language stays offered, only the order changes
        assert len(lang.LANGUAGE_CODES) == len(dict(lang.LANGUAGE_CHOICES))
        assert "en" in lang.LANGUAGE_CODES

    def test_refresh_keeps_object_identity_for_importers(self):
        """Importers bind these by name, so a rebind would never reach them."""
        locale_choices = lang.LOCALE_CHOICES
        language_codes = lang.LANGUAGE_CODES
        jsonform = catalog_common.LOCALE_CHOICES_JSONFORM

        lang.SITE_PREFERRED_LANGUAGES[:] = ["ja", "en"]
        lang.refresh_language_caches()

        assert locale_choices is lang.LOCALE_CHOICES
        assert language_codes is lang.LANGUAGE_CODES
        assert jsonform is catalog_common.LOCALE_CHOICES_JSONFORM
        assert jsonform[0]["value"] == "ja"

    def test_field_schema_built_before_refresh_follows_it(self):
        """Model fields capture the schema at class definition time."""
        field = catalog_common.LanguageListField()
        one_of = field.schema["items"]["oneOf"]

        lang.SITE_PREFERRED_LANGUAGES[:] = ["ja", "en"]
        lang.refresh_language_caches()

        assert one_of is field.schema["items"]["oneOf"]
        assert one_of[0]["const"] == "ja"
        assert one_of[-1] == {"title": "Other", "type": "string"}

    def test_script_field_schema_follows_refresh(self):
        field = catalog_common.LanguageListField(script=True)
        one_of = field.schema["items"]["oneOf"]

        lang.SITE_PREFERRED_LANGUAGES[:] = ["ja", "en"]
        lang.refresh_language_caches()

        assert one_of[0]["const"] == "ja"
        assert one_of[-1] == {"title": "Other", "type": "string"}


class TestLocalizedLabelText:
    """An empty entry must not mask a later one for the same language;
    label lists accrete from several external resources (#1806)."""

    def test_empty_entry_does_not_mask_later_entry(self):
        labels = [
            {"lang": "en", "text": ""},
            {"lang": "en", "text": "Real Name"},
        ]
        assert localized_label_text(labels, ["en"]) == "Real Name"

    def test_falls_through_to_next_locale(self):
        labels = [{"lang": "en", "text": "Real Name"}]
        assert localized_label_text(labels, ["fr", "en"]) == "Real Name"

    def test_none_when_no_locale_matches(self):
        labels = [{"lang": "en", "text": ""}]
        assert localized_label_text(labels, ["en"]) is None

    def test_none_for_empty_list(self):
        assert localized_label_text([], ["en"]) is None
