import pytest
from django.conf import settings
from django.test import Client
from django.utils import translation

from common.models import (
    SITE_PREFERRED_LANGUAGES,
    SITE_PREFERRED_LOCALES,
    detect_language,
)
from common.models.lang import _build_language_aliases, normalize_languages


@pytest.mark.django_db(databases="__all__")
class TestCommon:
    def test_detect_lang(self):
        assert detect_language("The Witcher 3: Wild Hunt") == "en"
        assert detect_language("巫师3：狂猎") == "zh-cn"
        assert detect_language("巫师3：狂猎 The Witcher 3: Wild Hunt") == "zh-cn"
        # Japanese: kana present
        assert detect_language("進撃の巨人") == "ja"
        assert detect_language("鬼滅の刃") == "ja"
        # Korean: hangul present
        assert detect_language("오징어 게임") == "ko"
        # Arabic script
        assert detect_language("ألف ليلة وليلة") == "ar"
        # Greek
        assert detect_language("Οδύσσεια") == "el"
        # Single ASCII word falls back to English
        assert detect_language("Game") == "en"
        # Empty / whitespace
        assert detect_language("") == "x"
        assert detect_language("   ") == "x"

    def test_detect_lang_japanese_without_kana(self):
        """Kanji-only titles are Japanese when they use a Japanese-exclusive
        character form (shinjitai/kokuji), which no statistical model detects."""
        assert detect_language("攻殻機動隊") == "ja"
        assert detect_language("新世紀福音戦士") == "ja"
        assert detect_language("囲碁") == "ja"
        assert detect_language("黒澤明") == "ja"
        assert detect_language("津軽海峡冬景色") == "ja"
        # Han text sharing all characters with Chinese stays Chinese unless the
        # caller knows better, since the two are written identically
        assert detect_language("東京物語") == "zh-tw"
        assert detect_language("東京物語", hint="ja") == "ja"
        assert detect_language("村上春樹", hint="ja") == "ja"
        # A hint only breaks the Han ja/zh tie. Simplified forms are no evidence
        # against Japanese (体 and 国 are both shinjitai and simplified
        # Chinese), so the hint still wins for any Han-only string...
        assert detect_language("三体", hint="ja") == "ja"
        # ...but a non-Han script is real evidence and always wins.
        assert detect_language("오징어 게임", hint="ja") == "ko"
        assert detect_language("となりのトトロ", hint="zh") == "ja"
        assert detect_language("Breaking Bad", hint="ja") == "en"

    def test_detect_lang_short_titles(self):
        """Short catalog titles: the dominant input shape for this function."""
        # traditional vs simplified Chinese
        assert detect_language("霸王別姬") == "zh-tw"
        assert detect_language("我不是药神") == "zh-cn"
        # English titles that langdetect used to mis-tag
        for title in ("Breaking Bad", "Pride and Prejudice", "A Love Supreme"):
            assert detect_language(title) == "en", title
        # Cyrillic is Russian, not Bulgarian/Macedonian
        assert detect_language("Война и мир") == "ru"
        assert detect_language("Иди и смотри") == "ru"
        # board game titles, where the leading article carries the signal
        assert detect_language("Die Siedler von Catan") == "de"
        assert detect_language("Los Olvidados") == "es"
        assert detect_language("Os Colonizadores") == "pt"
        assert detect_language("Il Postino") == "it"
        # an English title borrowing a foreign article stays English
        assert detect_language("El Camino: A Breaking Bad Movie") == "en"

    def test_detect_lang_bgg_top_ranked(self):
        """Real titles from the BoardGameGeek ranking, whose primary names are
        English-language catalog entries. langdetect scattered these across
        af/cs/de/nl/pl/so/tl/tr."""
        for title in (
            "Ark Nova",
            "Terraforming Mars",
            "Star Wars: Rebellion",
            "Spirit Island",
            "Gaia Project",
            "Twilight Struggle",
            "Through the Ages: A New Story of Civilization",
            "Sky Team",
            "Terra Mystica",
            "Blood Rage",
            "Power Grid",
            "Sleeping Gods",
            "Underwater Cities",
            "Mechs vs. Minions",
            "Bomb Busters",
            "Final Girl",
            "Tzolk'in: The Mayan Calendar",
            "Viticulture Essential Edition",
            "Endeavor: Deep Sea",
        ):
            assert detect_language(title) == "en", title
        # titles that really are not English keep their own language
        assert detect_language("Orléans") == "fr"
        assert detect_language("Le Havre") == "fr"
        assert detect_language("Puerto Rico") == "es"

    def test_detect_lang_is_deterministic(self):
        """The old langdetect backend returned different answers across calls
        for the same short string, which silently churned stored lang tags."""
        for title in ("Solo Tú", "Tokyo Story", "En Attendant Godot", "Fuga"):
            assert len({detect_language(title) for _ in range(8)}) == 1, title

    def test_lang_list(self):
        assert len(SITE_PREFERRED_LANGUAGES) >= 1
        assert len(SITE_PREFERRED_LOCALES) >= 1


@pytest.mark.django_db(databases="__all__")
class TestNormalizeLanguages:
    def test_empty_list(self):
        """Should return empty list for empty input"""
        assert normalize_languages([]) == []

    def test_already_valid_codes(self):
        """Should preserve already valid language codes"""
        assert normalize_languages(["en", "fr", "de"]) == ["en", "fr", "de"]
        assert normalize_languages(["EN", "FR", "DE"]) == ["en", "fr", "de"]
        assert normalize_languages(["zh-cn", "zh-tw"]) == ["zh-cn", "zh-tw"]

    def test_language_aliases(self):
        """Should normalize various language names to standard codes"""
        assert normalize_languages(["English", "Japanese", "Chinese"]) == [
            "en",
            "ja",
            "zh",
        ]
        assert normalize_languages(["英语", "日语", "中文"]) == ["en", "ja", "zh"]
        assert normalize_languages(["eng", "jpn", "chi"]) == ["en", "ja", "zh"]
        assert normalize_languages(["simplified chinese", "traditional chinese"]) == [
            "zh-cn",
            "zh-tw",
        ]
        assert normalize_languages(["简体中文", "繁体中文"]) == ["zh-cn", "zh-tw"]
        assert normalize_languages(["french", "Français", "法语"]) == ["fr"]

    def test_unknown_languages(self):
        """Should preserve unknown languages while stripping whitespace"""
        assert normalize_languages(["Klingon", " Elvish ", "Dothraki"]) == [
            "klingon",
            "elvish",
            "dothraki",
        ]

    def test_mixed_input(self):
        """Should handle a mix of valid codes, aliases, and unknown languages"""
        assert normalize_languages(["en", "French", "中文", "Klingon"]) == [
            "en",
            "fr",
            "zh",
            "klingon",
        ]

    def test_empty_strings_and_whitespace(self):
        """Should filter out empty strings and strings with only whitespace"""
        assert normalize_languages(["en", "", " ", "fr"]) == ["en", "fr"]

    def test_duplicates(self):
        """Should remove duplicates while preserving order"""
        assert normalize_languages(["en", "English", "fr", "en", "英语"]) == [
            "en",
            "fr",
        ]

    def test_build_language_aliases_includes_multiple_languages(self):
        """Should generate aliases from all supported UI languages"""
        aliases = _build_language_aliases()

        # Should have a substantial number of aliases
        assert len(aliases) > 100

        # Test that we have aliases from different languages for the same language code
        # English should have multiple aliases from different source languages
        en_aliases = [alias for alias, code in aliases.items() if code == "en"]
        assert len(en_aliases) > 5

        # French should have multiple aliases from different source languages
        fr_aliases = [alias for alias, code in aliases.items() if code == "fr"]
        assert len(fr_aliases) > 5

    def test_build_language_aliases_preserves_current_language(self):
        """Should preserve current language context after building aliases"""
        original_language = translation.get_language()

        # Build aliases
        _build_language_aliases()

        # Check that current language is preserved
        current_after = translation.get_language()
        assert original_language == current_after

    def test_build_language_aliases_includes_custom_aliases(self):
        """Should include both generated and custom aliases"""
        aliases = _build_language_aliases()

        # Should include custom English aliases
        assert aliases.get("english") == "en"
        assert aliases.get("英语") == "en"
        assert aliases.get("英文") == "en"

        # Should include custom Chinese aliases
        assert aliases.get("chinese") == "zh"
        assert aliases.get("中文") == "zh"
        assert aliases.get("simplified chinese") == "zh-cn"
        assert aliases.get("traditional chinese") == "zh-tw"

        # Should include ISO 639-2 codes
        assert aliases.get("eng") == "en"
        assert aliases.get("fra") == "fr"
        assert aliases.get("deu") == "de"


@pytest.mark.django_db(databases="__all__")
class TestNodeInfo:
    def test_nodeinfo_basic(self):
        client = Client()
        response = client.get("/nodeinfo/2.0/")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "2.0"
        assert data["software"]["name"] == "neodb"
        assert "activitypub" in data["protocols"]
        assert "features" in data["metadata"]

    def test_nodeinfo_federation_disabled(self):
        """When NO_FEDERATION is set, nodeinfo should include federation.enabled=false."""
        setup = type("Setup", (), {"NO_FEDERATION": True})()
        had_setup = hasattr(settings, "SETUP")
        original = getattr(settings, "SETUP", None)
        try:
            settings.SETUP = setup
            client = Client()
            response = client.get("/nodeinfo/2.0/")
            assert response.status_code == 200
            data = response.json()
            assert data["metadata"]["federation"] == {"enabled": False}
        finally:
            if had_setup:
                settings.SETUP = original
            else:
                delattr(settings, "SETUP")

    def test_nodeinfo_federation_not_disabled(self):
        """When SETUP is absent, nodeinfo should not include federation key."""
        client = Client()
        response = client.get("/nodeinfo/2.0/")
        assert response.status_code == 200
        data = response.json()
        assert "federation" not in data["metadata"]
