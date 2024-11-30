from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation


def activate_language_for_user(user, request=None):
    user_language = None
    if user and user.is_authenticated:
        user_language = getattr(user, "language", "")
    if not user_language:
        if request:
            user_language = translation.get_language_from_request(request)
        else:
            user_language = settings.LANGUAGE_CODE
        # if user_language in dict(settings.LANGUAGES).keys():
    translation.activate(user_language)
    if request:
        request.LANGUAGE_CODE = translation.get_language()


class LanguageMiddleware(LocaleMiddleware):
    def process_request(self, request):
        user = getattr(request, "user", None)
        activate_language_for_user(user, request)
