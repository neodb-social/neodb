import os
import secrets
import sys
import urllib.parse
from pathlib import Path
from typing import Annotated, Literal

import dj_database_url
import django_cache_url
import sentry_sdk
from corsheaders.defaults import default_headers
from pydantic import (
    AnyUrl,
    EmailStr,
    Field,
    UrlConstraints,
)
from pydantic_core import Url
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis import Redis
from rq import Queue

from takahe import __version__
from takahe.neodb import __version__ as __neodb_version__

BASE_DIR = Path(__file__).resolve().parent.parent


CacheBackendUrl = Annotated[
    Url,
    UrlConstraints(
        host_required=False, allowed_schemes=list(django_cache_url.BACKENDS.keys())
    ),
]

ImplicitHostname = Annotated[
    Url,
    UrlConstraints(host_required=False),
]


MediaBackendUrl = Annotated[
    Url,
    UrlConstraints(
        host_required=False, allowed_schemes=["s3", "s3-insecure", "gs", "local"]
    ),
]


def as_bool(v: str | list[str] | None):
    if v is None:
        return False

    if isinstance(v, str):
        v = [v]

    return v[0].lower() in ("true", "yes", "t", "1")


Environments = Literal["debug", "development", "production", "test"]

TAKAHE_ENV_FILE = os.environ.get(
    "TAKAHE_ENV_FILE", None if "pytest" in sys.modules else BASE_DIR / ".env"
)

if "pytest" in sys.modules:
    test_env = {
        "DATABASE_SERVER": "postgres://postgres@localhost/takahe",
        "DEBUG": "true",
        "ENVIRONMENT": "test",
    }
    for key, value in test_env.items():
        os.environ.setdefault(f"TAKAHE_{key}", value)


class Settings(BaseSettings):
    """
    Pydantic-powered settings, to provide consistent error messages, strong
    typing, consistent prefixes, .venv support, etc.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        env_prefix="TAKAHE_",
        env_file=TAKAHE_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    #: The default database.
    DATABASE_SERVER: ImplicitHostname | None = None

    #: Disable Federation, not for production use
    NO_FEDERATION: bool = False

    #: The currently running environment, used for things such as sentry
    #: error reporting.
    ENVIRONMENT: Environments = "development"

    #: Should django run in debug mode?
    DEBUG: bool = False

    #: Should the debug toolbar be loaded?
    DEBUG_TOOLBAR: bool = False

    #: Should we atttempt to import the 'local_settings.py'
    LOCAL_SETTINGS: bool = False

    #: Set a secret key used for signing values such as sessions. Randomized
    #: by default, so you'll logout everytime the process restarts.
    SECRET_KEY: str = Field(default_factory=lambda: "autokey-" + secrets.token_hex(128))

    #: Set a secret key used to protect the stator. Randomized by default.
    STATOR_TOKEN: str = Field(default_factory=lambda: secrets.token_hex(128))

    #: If set, a list of allowed values for the HOST header. The default value
    #: of '*' means any host will be accepted.
    ALLOWED_HOSTS: list[str] = Field(default_factory=lambda: ["*"])

    #: If set, a list of hosts to accept for CORS.
    CORS_HOSTS: list[str] = Field(default_factory=list)

    #: If set, a list of hosts to accept for CSRF.
    CSRF_HOSTS: list[str] = Field(default_factory=list)

    #: If enabled, trust the HTTP_X_FORWARDED_FOR header.
    USE_PROXY_HEADERS: bool = False

    #: An optional Sentry DSN for error reporting.
    SENTRY_DSN: str | None = None
    SENTRY_SAMPLE_RATE: float = 1.0
    SENTRY_TRACES_SAMPLE_RATE: float = 0.01
    SENTRY_CAPTURE_MESSAGES: bool = False
    SENTRY_EXPERIMENTAL_PROFILES_TRACES_SAMPLE_RATE: float = 0.0

    #: Fallback domain for links.
    MAIN_DOMAIN: str = "example.com"

    EMAIL_SERVER: AnyUrl = "console://localhost"
    EMAIL_FROM: EmailStr = "test@example.com"
    AUTO_ADMIN_EMAIL: EmailStr | None = None
    ERROR_EMAILS: list[EmailStr] | None = None

    #: If set, a list of user agents to completely disallow in robots.txt
    #: List formatting must be a valid JSON list, such as `["Agent1", "Agent2"]`
    ROBOTS_TXT_DISALLOWED_USER_AGENTS: list[str] = Field(default_factory=list)

    MEDIA_URL: str = "/media/"
    MEDIA_ROOT: str = str(BASE_DIR / "media")
    MEDIA_BACKEND: MediaBackendUrl | None = None

    #: S3 ACL to apply to all media objects when MEDIA_BACKEND is set to S3. If using a CDN
    #: and/or have public access blocked to buckets this will likely need to be 'private'
    MEDIA_BACKEND_S3_ACL: str = "public-read"

    #: Maximum filesize when uploading images. Increasing this may increase memory utilization
    #: because all images with a dimension greater than 2000px are resized to meet that limit, which
    #: is necessary for compatibility with Mastodon’s image proxy.
    MEDIA_MAX_IMAGE_FILESIZE_MB: int = 10

    #: Maximum filesize for Avatars. Remote avatars larger than this size will
    #: not be fetched and served from media, but served through the image proxy.
    AVATAR_MAX_IMAGE_FILESIZE_KB: int = 1000

    #: Maximum filesize for Emoji. Attempting to upload Local Emoji larger than this size will be
    #: blocked. Remote Emoji larger than this size will not be fetched and served from media, but
    #: served through the image proxy.
    EMOJI_MAX_IMAGE_FILESIZE_KB: int = 200

    #: Request timeouts to use when talking to other servers Either
    #: float or tuple of floats for (connect, read, write, pool)
    REMOTE_TIMEOUT: float | tuple[float, float, float, float] = 5.0

    #: If search features like full text search should be enabled.
    #: (placeholder setting, no effect)
    SEARCH: bool = True

    #: Default cache backend
    CACHES_DEFAULT: CacheBackendUrl | None = None

    # How long to wait, in days, until remote posts/profiles are pruned from
    # our database if nobody local has interacted with them.
    # Set to zero to disable.
    REMOTE_PRUNE_HORIZON: int = 90

    # Remote posts older than this will not be pushed to timeline.
    FANOUT_LIMIT_DAYS: int = 9

    # Stator tuning
    STATOR_CONCURRENCY: int = 20
    STATOR_CONCURRENCY_PER_MODEL: int = 4

    # Web Push keys
    # Generate via https://web-push-codelab.glitch.me/
    VAPID_PUBLIC_KEY: str | None = None
    VAPID_PRIVATE_KEY: str | None = None

    PGHOST: str | None = Field(None, alias="PGHOST")
    PGPORT: int | None = Field(5432, alias="PGPORT")
    PGNAME: str = Field("takahe", alias="PGNAME")
    PGUSER: str = Field("postgres", alias="PGUSER")
    PGPASSWORD: str | None = Field(None, alias="PGPASSWORD")


SETUP = Settings()

# Don't allow automatic keys in production
if not SETUP.DEBUG and SETUP.SECRET_KEY.startswith("autokey-"):
    print("You must set TAKAHE_SECRET_KEY in production")
    sys.exit(1)
SECRET_KEY = SETUP.SECRET_KEY
DEBUG = SETUP.DEBUG

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "corsheaders",
    "django_htmx",
    "hatchway",
    "core",
    "activities",
    "api",
    "mediaproxy",
    "stator",
    "users",
]

MIDDLEWARE = [
    "core.middleware.SentryTaggingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",  # request.session
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",  # request.user
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "users.middleware.DomainMiddleware",  # request.domain
    "api.middleware.ApiTokenMiddleware",  # request.token, request.identity
    "core.middleware.ConfigLoadingMiddleware",  # request.config
    "core.middleware.HeadersMiddleware",
    "core.middleware.ParamsMiddleware",  # request.PARAMS
]

ROOT_URLCONF = "takahe.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context.config_context",
                "users.context.user_context",
            ],
        },
    },
]

WSGI_APPLICATION = "takahe.wsgi.application"

if SETUP.DATABASE_SERVER:
    DATABASES = {
        "default": dj_database_url.parse(str(SETUP.DATABASE_SERVER), conn_max_age=600)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": SETUP.PGHOST,
            "PORT": SETUP.PGPORT,
            "NAME": SETUP.PGNAME,
            "USER": SETUP.PGUSER,
            "PASSWORD": SETUP.PGPASSWORD,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "users.User"

LOGIN_URL = "/auth/login/"
LOGOUT_URL = "/auth/logout/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
    },
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
}

SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

WHITENOISE_MAX_AGE = 3600

STATIC_ROOT = BASE_DIR / "static-collected"

ALLOWED_HOSTS = SETUP.ALLOWED_HOSTS

AUTO_ADMIN_EMAIL = SETUP.AUTO_ADMIN_EMAIL

STATOR_TOKEN = SETUP.STATOR_TOKEN
STATOR_CONCURRENCY = SETUP.STATOR_CONCURRENCY
STATOR_CONCURRENCY_PER_MODEL = SETUP.STATOR_CONCURRENCY_PER_MODEL

ROBOTS_TXT_DISALLOWED_USER_AGENTS = SETUP.ROBOTS_TXT_DISALLOWED_USER_AGENTS

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOWED_ORIGINS = SETUP.CORS_HOSTS
CORS_PREFLIGHT_MAX_AGE = 604800
CORS_EXPOSE_HEADERS = ("link",)
CORS_ALLOW_HEADERS = (*default_headers, "Idempotency-Key")

JSONLD_MAX_SIZE = 1024 * 50  # 50 KB

CSRF_TRUSTED_ORIGINS = SETUP.CSRF_HOSTS

MEDIA_URL = SETUP.MEDIA_URL
MEDIA_ROOT = SETUP.MEDIA_ROOT
MAIN_DOMAIN = SETUP.MAIN_DOMAIN

if not DEBUG and MAIN_DOMAIN == "example.com":
    raise ValueError("You must set a TAKAHE_MAIN_DOMAIN!")

# Debug toolbar should only be loaded at all when debug is on
if DEBUG and SETUP.DEBUG_TOOLBAR:
    INSTALLED_APPS.append("debug_toolbar")
    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": "core.middleware.show_toolbar"}
    MIDDLEWARE.insert(8, "debug_toolbar.middleware.DebugToolbarMiddleware")

if SETUP.USE_PROXY_HEADERS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


if SETUP.SENTRY_DSN:
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.httpx import HttpxIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_experiments = {}

    if SETUP.SENTRY_EXPERIMENTAL_PROFILES_TRACES_SAMPLE_RATE > 0:
        sentry_experiments["profiles_sample_rate"] = (
            SETUP.SENTRY_EXPERIMENTAL_PROFILES_TRACES_SAMPLE_RATE
        )

    sentry_sdk.init(
        dsn=SETUP.SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            HttpxIntegration(),
            LoggingIntegration(),
        ],
        traces_sample_rate=SETUP.SENTRY_TRACES_SAMPLE_RATE,
        sample_rate=SETUP.SENTRY_SAMPLE_RATE,
        send_default_pii=True,
        environment=SETUP.ENVIRONMENT,
        _experiments=sentry_experiments,
    )
    sentry_sdk.set_tag("takahe.version", __version__)

SERVER_EMAIL = SETUP.EMAIL_FROM
if SETUP.EMAIL_SERVER:
    query = urllib.parse.parse_qs(SETUP.EMAIL_SERVER.query)
    if SETUP.EMAIL_SERVER.scheme == "console":
        EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    elif SETUP.EMAIL_SERVER.scheme == "sendgrid":
        EMAIL_HOST = "smtp.sendgrid.net"
        EMAIL_PORT = 587
        EMAIL_HOST_USER = "apikey"
        # urlparse will lowercase it
        EMAIL_HOST_PASSWORD = SETUP.EMAIL_SERVER.host
        EMAIL_USE_TLS = True
    elif SETUP.EMAIL_SERVER.scheme == "smtp":
        EMAIL_HOST = SETUP.EMAIL_SERVER.host
        EMAIL_PORT = SETUP.EMAIL_SERVER.port
        if SETUP.EMAIL_SERVER.username is not None:
            EMAIL_HOST_USER = urllib.parse.unquote(SETUP.EMAIL_SERVER.username)
        if SETUP.EMAIL_SERVER.password is not None:
            EMAIL_HOST_PASSWORD = urllib.parse.unquote(SETUP.EMAIL_SERVER.password)
        EMAIL_USE_TLS = as_bool(query.get("tls"))
        EMAIL_USE_SSL = as_bool(query.get("ssl"))
    else:
        raise ValueError("Unknown schema for EMAIL_SERVER.")


if SETUP.MEDIA_BACKEND:
    query = urllib.parse.parse_qs(SETUP.MEDIA_BACKEND.query)
    path = SETUP.MEDIA_BACKEND.path or ""
    if SETUP.MEDIA_BACKEND.scheme == "gs":
        STORAGES["default"]["BACKEND"] = "core.uploads.TakaheGoogleCloudStorage"
        GS_BUCKET_NAME = path.lstrip("/")
        GS_QUERYSTRING_AUTH = False
        if SETUP.MEDIA_BACKEND.host is not None:
            port = SETUP.MEDIA_BACKEND.port or 443
            GS_CUSTOM_ENDPOINT = f"https://{SETUP.MEDIA_BACKEND.host}:{port}"
    elif (SETUP.MEDIA_BACKEND.scheme == "s3") or (
        SETUP.MEDIA_BACKEND.scheme == "s3-insecure"
    ):
        STORAGES["default"]["BACKEND"] = "core.uploads.TakaheS3Storage"
        AWS_STORAGE_BUCKET_NAME = path.lstrip("/")
        AWS_QUERYSTRING_AUTH = False
        AWS_DEFAULT_ACL = SETUP.MEDIA_BACKEND_S3_ACL
        if SETUP.MEDIA_BACKEND.username is not None:
            AWS_ACCESS_KEY_ID = SETUP.MEDIA_BACKEND.username
            AWS_SECRET_ACCESS_KEY = urllib.parse.unquote(
                SETUP.MEDIA_BACKEND.password or ""
            )
        if SETUP.MEDIA_BACKEND.host is not None:
            if SETUP.MEDIA_BACKEND.scheme == "s3-insecure":
                s3_default_port = 80
                s3_scheme = "http"
            else:
                s3_default_port = 443
                s3_scheme = "https"
            port = SETUP.MEDIA_BACKEND.port or s3_default_port
            AWS_S3_ENDPOINT_URL = f"{s3_scheme}://{SETUP.MEDIA_BACKEND.host}:{port}"
        if SETUP.MEDIA_URL is not None:
            media_url_parsed = urllib.parse.urlparse(SETUP.MEDIA_URL)
            AWS_S3_CUSTOM_DOMAIN = (
                media_url_parsed.hostname or ""
            ) + media_url_parsed.path.rstrip("/")
    elif SETUP.MEDIA_BACKEND.scheme == "local":
        if not (MEDIA_ROOT and MEDIA_URL):
            raise ValueError(
                "You must provide MEDIA_ROOT and MEDIA_URL for a local media backend"
            )
        if "://" not in MEDIA_URL and not DEBUG:
            raise ValueError(
                "The MEDIA_URL setting must start with https://your-domain"
            )
    else:
        raise ValueError(f"Unsupported media backend {SETUP.MEDIA_BACKEND.scheme}")

CACHES = {
    "default": django_cache_url.parse(str(SETUP.CACHES_DEFAULT or "dummy://")),
}

if SETUP.ERROR_EMAILS:
    ADMINS = [("Admin", e) for e in SETUP.ERROR_EMAILS]

TAKAHE_USER_AGENT = (
    f"NeoDB/{__neodb_version__} (Takahe/{__version__}; +https://{SETUP.MAIN_DOMAIN}/)"
)

FANOUT_LIMIT_DAYS = SETUP.FANOUT_LIMIT_DAYS

if SETUP.LOCAL_SETTINGS:
    # Let any errors bubble up
    from .local_settings import *  # noqa

NEODB_MQ = Queue(
    "ap", connection=Redis.from_url(str(SETUP.CACHES_DEFAULT or "redis://localhost"))
)
