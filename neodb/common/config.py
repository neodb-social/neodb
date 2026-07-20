from urllib import parse

import environ
from django.core.exceptions import ImproperlyConfigured


def resolve_email_settings(email_url: object, debug: bool) -> dict[str, object]:
    """Resolve an email URL into settings that can be applied at runtime."""
    config: dict[str, object] = {
        "EMAIL_BACKEND": "django.core.mail.backends.dummy.EmailBackend",
        "EMAIL_USE_TLS": False,
        "EMAIL_USE_SSL": False,
        "ANYMAIL": {},
        "ENABLE_LOGIN_EMAIL": False,
    }
    if not isinstance(email_url, str) or not email_url:
        return config
    parsed_email_url = parse.urlparse(email_url)
    if parsed_email_url.scheme == "anymail":
        if not parsed_email_url.hostname:
            raise ImproperlyConfigured("Anymail URL must include a backend name")
        config["EMAIL_BACKEND"] = (
            f"anymail.backends.{parsed_email_url.hostname}.EmailBackend"
        )
        anymail: dict[str, object] = dict(parse.parse_qsl(parsed_email_url.query))
        if debug:
            anymail["DEBUG_API_REQUESTS"] = True
        config["ANYMAIL"] = anymail
        config["ENABLE_LOGIN_EMAIL"] = True
    elif debug and parsed_email_url.scheme == "console":
        config["EMAIL_BACKEND"] = "django.core.mail.backends.console.EmailBackend"
        config["ENABLE_LOGIN_EMAIL"] = True
    elif parsed_email_url.scheme:
        if parsed_email_url.scheme.startswith("smtp") and not parsed_email_url.hostname:
            raise ImproperlyConfigured("SMTP URL must include a host")
        config.update(environ.Env.email_url_config(email_url))
        config["EMAIL_TIMEOUT"] = 5
        config["ENABLE_LOGIN_EMAIL"] = True
    return config


# how many items are showed in one search result page
ITEMS_PER_PAGE = 20
ITEMS_PER_PAGE_OPTIONS = [20, 40, 80]

# how many pages links in the pagination
PAGE_LINK_NUMBER = 7

# max tags on list page
TAG_NUMBER_ON_LIST = 5

# how many books have in each set at the home page
BOOKS_PER_SET = 5

# how many movies have in each set at the home page
MOVIES_PER_SET = 5

# how many music items have in each set at the home page
MUSIC_PER_SET = 5

# how many games have in each set at the home page
GAMES_PER_SET = 5
