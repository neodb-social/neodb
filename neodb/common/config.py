import json
import re
from urllib import parse

import environ
from django.core.exceptions import ImproperlyConfigured

# Variable names that hold a credential outright, e.g. NEODB_SECRET_KEY,
# PGPASSWORD, TAKAHE_STATOR_TOKEN, TAKAHE_VAPID_PRIVATE_KEY.
_SECRET_NAME_RE = re.compile(r"SECRET|PASSWORD|PASSWD|TOKEN|_KEY(_|$)")
_SECRET_PARAM_RE = re.compile(r"key|token|secret|pass|pwd|auth|credential|sig", re.I)
MASK = "********"


def mask_secret(name: str, value: str) -> str:
    """Hide credentials in a setting value before it is shown to an admin.

    The whole value is hidden when the variable name says it is a secret,
    unless the name marks it PUBLIC. A URL keeps its username, host and path
    but loses the password and sensitive query parameters; a DSN loses the
    whole userinfo, because Sentry puts the key in the username slot.
    """
    if not value:
        return value
    upper = name.upper()
    if _SECRET_NAME_RE.search(upper) and "PUBLIC" not in upper:
        return MASK
    if "://" not in value:
        return value
    parts = parse.urlsplit(value)
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        if "DSN" in upper:
            userinfo = MASK
        elif ":" in userinfo:
            userinfo = f"{userinfo.split(':', 1)[0]}:{MASK}"
        netloc = f"{userinfo}@{host}"
    # Work on the raw query so the original encoding of kept values survives.
    pairs = []
    for pair in parts.query.split("&") if parts.query else []:
        key = pair.partition("=")[0]
        pairs.append(f"{key}={MASK}" if _SECRET_PARAM_RE.search(key) else pair)
    query = "&".join(pairs)
    return parse.urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def format_config_value(name: str, value: object) -> str:
    """Render a setting for display, with credentials masked."""
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return ", ".join(mask_secret(name, str(v)) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return mask_secret(name, str(value))


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
