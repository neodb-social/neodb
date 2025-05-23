from django.test import TestCase
from django.utils import translation

from common.models.lang import _build_language_aliases, normalize_languages


class NormalizeLanguagesTestCase(TestCase):
    databases = "__all__"

    def test_empty_list(self):
        """Should return empty list for empty input"""
        self.assertEqual(normalize_languages([]), [])
        self.assertEqual(normalize_languages(None), [])  # type:ignore

    def test_already_valid_codes(self):
        """Should preserve already valid language codes"""
        self.assertEqual(normalize_languages(["en", "fr", "de"]), ["en", "fr", "de"])
        self.assertEqual(normalize_languages(["EN", "FR", "DE"]), ["en", "fr", "de"])
        self.assertEqual(normalize_languages(["zh-cn", "zh-tw"]), ["zh-cn", "zh-tw"])

    def test_language_aliases(self):
        """Should normalize various language names to standard codes"""
        self.assertEqual(
            normalize_languages(["English", "Japanese", "Chinese"]), ["en", "ja", "zh"]
        )
        self.assertEqual(
            normalize_languages(["英语", "日语", "中文"]), ["en", "ja", "zh"]
        )
        self.assertEqual(normalize_languages(["eng", "jpn", "chn"]), ["en", "ja", "zh"])
        self.assertEqual(
            normalize_languages(["simplified chinese", "traditional chinese"]),
            ["zh-cn", "zh-tw"],
        )
        self.assertEqual(
            normalize_languages(["简体中文", "繁体中文"]), ["zh-cn", "zh-tw"]
        )
        self.assertEqual(
            normalize_languages(["french", "Français", "法语"]),
            ["fr"],
        )

    def test_unknown_languages(self):
        """Should preserve unknown languages while stripping whitespace"""
        self.assertEqual(
            normalize_languages(["Klingon", " Elvish ", "Dothraki"]),
            ["klingon", "elvish", "dothraki"],
        )

    def test_mixed_input(self):
        """Should handle a mix of valid codes, aliases, and unknown languages"""
        self.assertEqual(
            normalize_languages(["en", "French", "中文", "Klingon"]),
            ["en", "fr", "zh", "klingon"],
        )

    def test_empty_strings_and_whitespace(self):
        """Should filter out empty strings and strings with only whitespace"""
        self.assertEqual(normalize_languages(["en", "", " ", "fr"]), ["en", "fr"])

    def test_duplicates(self):
        """Should remove duplicates while preserving order"""
        self.assertEqual(
            normalize_languages(["en", "English", "fr", "en", "英语"]), ["en", "fr"]
        )

    def test_build_language_aliases_includes_multiple_languages(self):
        """Should generate aliases from all supported UI languages"""
        aliases = _build_language_aliases()

        # Should have a substantial number of aliases
        self.assertGreater(len(aliases), 100)

        # Test that we have aliases from different languages for the same language code
        # English should have multiple aliases from different source languages
        en_aliases = [alias for alias, code in aliases.items() if code == "en"]
        self.assertGreater(len(en_aliases), 5)

        # French should have multiple aliases from different source languages
        fr_aliases = [alias for alias, code in aliases.items() if code == "fr"]
        self.assertGreater(len(fr_aliases), 5)

    def test_build_language_aliases_preserves_current_language(self):
        """Should preserve current language context after building aliases"""
        original_language = translation.get_language()

        # Build aliases
        _build_language_aliases()

        # Check that current language is preserved
        current_after = translation.get_language()
        self.assertEqual(original_language, current_after)

    def test_build_language_aliases_includes_custom_aliases(self):
        """Should include both generated and custom aliases"""
        aliases = _build_language_aliases()

        # Should include custom English aliases
        self.assertEqual(aliases.get("english"), "en")
        self.assertEqual(aliases.get("英语"), "en")
        self.assertEqual(aliases.get("英文"), "en")

        # Should include custom Chinese aliases
        self.assertEqual(aliases.get("chinese"), "zh")
        self.assertEqual(aliases.get("中文"), "zh")
        self.assertEqual(aliases.get("simplified chinese"), "zh-cn")
        self.assertEqual(aliases.get("traditional chinese"), "zh-tw")

        # Should include ISO 639-2 codes
        self.assertEqual(aliases.get("eng"), "en")
        self.assertEqual(aliases.get("fra"), "fr")
        self.assertEqual(aliases.get("deu"), "de")
