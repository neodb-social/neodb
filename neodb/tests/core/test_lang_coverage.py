from django.utils import translation

from common.models.lang import get_current_locales, localize_number


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
