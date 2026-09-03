import json
import re
from typing import Any
from urllib import parse

import environ
from django.core.exceptions import ImproperlyConfigured
from django.views.debug import SafeExceptionReporterFilter

# Names of variables, dict keys and URL query parameters that hold a credential,
# e.g. NEODB_SECRET_KEY, PGPASSWORD, TAKAHE_STATOR_TOKEN, api_key, sslpassword.
_SECRET_RE = re.compile(
    r"secret|passw|pwd|token|credential|private|auth|apikey"
    r"|(^|_)pass(_|$)|(^|_)keys?(_|$)|(^|_)sig(nature)?(_|$)",
    re.I,
)
# URL schemes whose host part is a plain host or backend name. Any other scheme
# may carry the credential in the host slot (sendgrid://<API_KEY>), so its
# whole netloc is hidden.
_HOST_IS_PUBLIC_SCHEMES = frozenset(
    {
        "http",
        "https",
        "ws",
        "wss",
        "postgres",
        "postgresql",
        "psql",
        "pgsql",
        "redis",
        "rediss",
        "memcache",
        "memcached",
        "typesense",
        "s3",
        "s3-insecure",
        "gcs",
        "local",
        "file",
        "smtp",
        "smtp+tls",
        "smtp+ssl",
        "console",
        "anymail",
    }
)
MASK = "********"


def _is_secret_name(name: str) -> bool:
    return bool(_SECRET_RE.search(name)) and "public" not in name.lower()


def mask_secret(name: str, value: str) -> str:
    """Hide credentials in a setting value before it is shown to an admin.

    The whole value is hidden when the variable name says it is a secret,
    unless the name marks it PUBLIC. A URL keeps its username, host and path
    but loses the password and sensitive query parameters; a DSN loses the
    whole userinfo, because Sentry puts the key in the username slot, and a
    URL with an unknown scheme loses its whole netloc. A value that looks like
    a URL but does not parse is hidden rather than risk showing a credential.
    """
    if not value:
        return value
    if _is_secret_name(name):
        return MASK
    if "://" not in value:
        return value
    try:
        parts = parse.urlsplit(value)
    except ValueError:
        return MASK
    netloc = parts.netloc
    if parts.scheme.lower() not in _HOST_IS_PUBLIC_SCHEMES:
        netloc = MASK
    elif "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        if "dsn" in name.lower():
            userinfo = MASK
        elif ":" in userinfo:
            userinfo = f"{userinfo.split(':', 1)[0]}:{MASK}"
        netloc = f"{userinfo}@{host}"
    # Work on the raw query so the original encoding of kept values survives.
    pairs = []
    for pair in parts.query.split("&") if parts.query else []:
        key = pair.partition("=")[0]
        pairs.append(f"{key}={MASK}" if _SECRET_RE.search(key) else pair)
    query = "&".join(pairs)
    return parse.urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def format_config_value(name: str, value: object) -> str:
    """Render a setting for display, with credentials masked."""
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return ", ".join(mask_secret(name, str(v)) for v in value)
    if isinstance(value, dict):
        # each key names its value, e.g. {"api_key": ...} in a connection dict
        return json.dumps(
            {k: mask_secret(str(k), str(v)) for k, v in value.items()},
            ensure_ascii=False,
        )
    return mask_secret(name, str(value))


class ConfigExceptionReporterFilter(SafeExceptionReporterFilter):
    """Debug 500 page filter that also hides credentials inside URL settings.

    Django hides settings by name only (KEY, PASS, SECRET, ...). Connection
    strings such as DB_URL, REDIS_URL, MEDIA_BACKEND or SENTRY_DSN carry the
    credential in the value, so mask those the same way the Environment
    settings page does.
    """

    def cleanse_setting(self, key: int | str, value: Any) -> Any:
        cleansed = super().cleanse_setting(key, value)
        if isinstance(cleansed, str) and "://" in cleansed:
            return mask_secret(str(key), cleansed)
        return cleansed


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
