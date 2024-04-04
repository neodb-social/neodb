from django.conf import settings
from django.utils import translation
from django.utils.deprecation import MiddlewareMixin


class LanguageMiddleware(MiddlewareMixin):
    def process_request(self, request):
        user_language = settings.LANGUAGE_CODE
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            user_language = getattr(user, "language", "")
            if user_language not in dict(settings.LANGUAGES).keys():
                user_language = settings.LANGUAGE_CODE
        current_language = translation.get_language()
        if user_language != current_language:
            translation.activate(user_language)
