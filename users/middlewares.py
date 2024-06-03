from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation


class LanguageMiddleware(LocaleMiddleware):
    def process_request(self, request):
        user_language = None
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            user_language = getattr(user, "language", "")
        if not user_language:
            user_language = translation.get_language_from_request(request)
            # if user_language in dict(settings.LANGUAGES).keys():
        translation.activate(user_language)
        request.LANGUAGE_CODE = translation.get_language()
