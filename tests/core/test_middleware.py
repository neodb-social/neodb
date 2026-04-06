from unittest.mock import MagicMock

from django.test import RequestFactory
from django.utils import translation

from users.middlewares import activate_language_for_user

_rf = RequestFactory()


class TestActivateLanguageForUser:
    def _make_request(self, lang_param=None):
        path = f"/?lang={lang_param}" if lang_param else "/"
        return _rf.get(path)

    def test_authenticated_user_with_language(self):
        user = MagicMock()
        user.is_authenticated = True
        user.language = "zh-hans"
        request = self._make_request()

        activate_language_for_user(user, request)

        assert translation.get_language() == "zh-hans"

    def test_authenticated_user_without_language(self):
        user = MagicMock()
        user.is_authenticated = True
        user.language = ""
        request = self._make_request()

        activate_language_for_user(user, request)

        # should fall back to request-based language detection
        assert translation.get_language() is not None

    def test_unauthenticated_user(self):
        user = MagicMock()
        user.is_authenticated = False
        request = self._make_request()

        activate_language_for_user(user, request)

        assert translation.get_language() is not None

    def test_none_user_with_request(self):
        request = self._make_request()

        activate_language_for_user(None, request)

        assert translation.get_language() is not None

    def test_none_user_no_request(self, settings):
        settings.LANGUAGE_CODE = "en-us"

        activate_language_for_user(None, None)

        assert translation.get_language() == "en-us"

    def test_lang_param_override(self):
        user = MagicMock()
        user.is_authenticated = True
        user.language = ""
        request = self._make_request(lang_param="en")

        activate_language_for_user(user, request)

        assert translation.get_language() == "en"

    def test_invalid_lang_param_fallback(self):
        user = MagicMock()
        user.is_authenticated = True
        user.language = ""
        request = self._make_request(lang_param="zzz-invalid")

        activate_language_for_user(user, request)

        # should not raise, should fall back
        assert translation.get_language() is not None

    def test_sets_request_language_code(self):
        user = MagicMock()
        user.is_authenticated = True
        user.language = "en"
        request = self._make_request()

        activate_language_for_user(user, request)

        assert hasattr(request, "LANGUAGE_CODE")
        assert request.LANGUAGE_CODE == "en"
