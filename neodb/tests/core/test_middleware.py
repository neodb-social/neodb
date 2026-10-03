from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from django.utils import translation

from users.middlewares import activate_language_for_user

_rf = RequestFactory()


class TestActivateLanguageForUser:
    def _make_request(self, lang_param: str | None = None) -> HttpRequest:
        path = f"/?lang={lang_param}" if lang_param else "/"
        return _rf.get(path, HTTP_ACCEPT_LANGUAGE="fr")

    def test_authenticated_user_with_language(self):
        user = MagicMock()
        user.is_authenticated = True
        user.language = "zh-hans"
        request = self._make_request()

        activate_language_for_user(user, request)

        assert translation.get_language() == "zh-hans"

    @pytest.mark.parametrize(
        ("authenticated", "lang_param"),
        [(True, None), (False, None), (None, None), (True, "zzz-invalid")],
    )
    def test_request_language_fallback(
        self, authenticated: bool | None, lang_param: str | None
    ) -> None:
        user = (
            MagicMock(is_authenticated=authenticated, language="")
            if authenticated is not None
            else None
        )
        request = self._make_request(lang_param)
        with translation.override("en"):
            activate_language_for_user(user, request)

            assert translation.get_language() == "fr"
            assert request.LANGUAGE_CODE == "fr"

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

    def test_sets_request_language_code(self):
        user = MagicMock()
        user.is_authenticated = True
        user.language = "en"
        request = self._make_request()

        activate_language_for_user(user, request)

        assert hasattr(request, "LANGUAGE_CODE")
        assert request.LANGUAGE_CODE == "en"
