from django.test import TestCase

from common.models import detect_language


class CommonTestCase(TestCase):
    databases = "__all__"

    def test_detect_lang(self):
        lang = detect_language("The Witcher 3: Wild Hunt")
        self.assertEqual(lang, "en")
        lang = detect_language("巫师3：狂猎")
        self.assertEqual(lang, "zh-cn")
        lang = detect_language("巫师3：狂猎 The Witcher 3: Wild Hunt")
        self.assertEqual(lang, "zh-cn")
