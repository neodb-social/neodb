from django.test import TestCase

from common.models import (
    SITE_PREFERRED_LANGUAGES,
    SITE_PREFERRED_LOCALES,
    detect_language,
)


class CommonTestCase(TestCase):
    databases = "__all__"

    def test_detect_lang(self):
        lang = detect_language("The Witcher 3: Wild Hunt")
        self.assertEqual(lang, "en")
        lang = detect_language("巫师3：狂猎")
        self.assertEqual(lang, "zh-cn")
        lang = detect_language("巫师3：狂猎 The Witcher 3: Wild Hunt")
        self.assertEqual(lang, "zh-cn")

    def test_lang_list(self):
        self.assertGreaterEqual(len(SITE_PREFERRED_LANGUAGES), 1)
        self.assertGreaterEqual(len(SITE_PREFERRED_LOCALES), 1)
