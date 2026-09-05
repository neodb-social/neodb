import functools
from typing import ClassVar

import pydantic
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.db.utils import DatabaseError, ProgrammingError
from django.utils.translation import trans_real
from loguru import logger

from common.config import resolve_email_settings
from common.models.genre import DEFAULT_GENRE_CATEGORIES

# Bounds for SystemOptions.registration_captcha_items; 0 disables the captcha.
CAPTCHA_MIN_ITEMS = 4
CAPTCHA_MAX_ITEMS = 8


class SiteConfig(models.Model):
    """
    Singleton model storing site-wide configuration as a single JSON blob.

    Only the row with pk=1 is used. Access the current config via the
    class-level ``SiteConfig.system`` attribute, which is auto-loaded
    on first access and refreshed periodically by ``SiteConfigMiddleware``.
    """

    data = models.JSONField(default=dict)

    class Meta:
        db_table = "common_siteconfig"

    system: ClassVar["SiteConfig.SystemOptions"]

    class SystemOptions(pydantic.BaseModel):
        # Branding
        site_name: str = ""
        site_logo: str = "/s/img/logo.svg"
        site_icon: str = "/s/img/icon.png"
        user_icon: str = "/s/img/avatar.png"
        site_color: str = "azure"
        site_intro: str = ""
        site_head: str = ""
        site_description: str = "reviews about book, film, music, podcast and game."
        site_links: dict = {}

        # Access Control
        invite_only: bool = False
        enable_local_only: bool = False
        mastodon_login_whitelist: list[str] = []
        # Number of covers shown in the registration captcha; 0 disables it.
        registration_captcha_items: int = 0
        # Marks an item needs before the captcha considers it recognizable.
        min_marks_for_captcha: int = 10

        # Auth Options
        enable_login_mastodon: bool = True
        enable_login_bluesky: bool = False
        enable_login_threads: bool = False
        email_url: str = ""
        email_from: str = ""

        # Discover
        min_marks_for_discover: int = 1
        discover_update_interval: int = 60
        discover_filter_language: bool = False
        discover_show_local_only: bool = False
        discover_show_popular_posts: bool = False
        discover_show_popular_tags: bool = False
        discover_show_verified_podcasts: bool = False

        # Recommendations (off by default; test-enabled users can preview)
        enable_recommendations: bool = False
        reco_min_source_marks: int = 3
        reco_min_target_marks: int = 2
        reco_similarity_top_k: int = 50
        reco_user_top_n: int = 100
        reco_user_idf_dampen: bool = True
        reco_user_mark_cap: int = 500
        reco_user_active_days: int = 30
        reco_per_user_seed_cap: int = 200
        reco_lazy_ttl_days: int = 7
        reco_circles_window_days: int = 30

        # Localization
        preferred_languages: list[str] = ["en", "zh"]
        # Default UI language for visitors and users with no language preference.
        # Unlike preferred_languages this keeps the sub-tag (e.g. zh-hant), so it
        # cannot be derived from that list.
        language_code: str = "en"

        # Federation
        disable_default_relay: bool = False
        fanout_limit_days: int = 9
        remote_prune_horizon: int = 92

        # Search/Catalog
        search_sites: list[str] = []
        search_peers: list[str] = []
        hidden_categories: list[str] = []
        # Highest search result page an anonymous visitor may open.
        guest_search_max_pages: int = 100

        # Catalog genres: slugs offered in each category's edit dropdown.
        # Empty list falls back to the in-code default (DEFAULT_GENRE_CATEGORIES).
        genres_movie: list[str] = DEFAULT_GENRE_CATEGORIES["movie"]
        genres_tv: list[str] = DEFAULT_GENRE_CATEGORIES["tv"]
        genres_music: list[str] = DEFAULT_GENRE_CATEGORIES["music"]
        genres_game: list[str] = DEFAULT_GENRE_CATEGORIES["game"]
        genres_podcast: list[str] = DEFAULT_GENRE_CATEGORIES["podcast"]
        genres_performance: list[str] = DEFAULT_GENRE_CATEGORIES["performance"]

        # API Keys - Catalog
        spotify_api_key: str = ""
        tmdb_api_key: str = "TESTONLY"
        google_api_key: str = ""
        discogs_api_key: str = "TESTONLY"
        igdb_client_id: str = "TESTONLY"
        igdb_client_secret: str = ""
        bgg_api_token: str = ""
        mal_client_id: str = ""

        # API Keys - Services
        steam_api_key: str = ""
        deepl_api_key: str = ""
        lt_api_url: str = ""
        lt_api_key: str = ""
        threads_app_id: str = ""
        threads_app_secret: str = ""

        # Notifications
        discord_webhooks: dict = {}

        # Downloader
        downloader_proxy_list: list[str] = []
        downloader_backup_proxy: str = ""
        downloader_providers: str = ""
        downloader_scrapfly_key: str = ""
        downloader_decodo_token: str = ""
        downloader_scraperapi_key: str = ""
        downloader_scrapingbee_key: str = ""
        downloader_customscraper_url: str = ""
        downloader_request_timeout: int = 90
        downloader_cache_timeout: int = 300
        downloader_retries: int = 3

        # Cleanup
        task_cleanup_days: int = 28

        # Advanced / Operational
        # auto-generated ES256 key (JWK) for the ATProto OAuth client;
        # managed by mastodon.models.bluesky_oauth, not exposed in the UI
        atproto_client_jwk: str = ""
        alternative_domains: list[str] = []
        mastodon_client_scope: str = (
            "read:accounts read:follows read:search"
            " read:blocks read:mutes"
            " write:statuses write:media"
        )
        mastodon_timeout: int = 5
        disable_cron_jobs: list[str] = []
        index_aliases: dict = {"catalog": "catalog2"}
        skip_migrations: list[str] = []

        @pydantic.field_validator("registration_captcha_items")
        @classmethod
        def validate_registration_captcha_items(cls, value: int) -> int:
            # One correct assignment out of 2**n - 2 plausible ones: with the
            # three attempts the flow allows, 2 tiles pass by guessing ~87% of
            # the time and 3 tiles ~42%, so anything below 4 is decorative.
            if value and value < CAPTCHA_MIN_ITEMS:
                raise ValueError(
                    f"{value} is too few to sort: use 0 to disable the captcha,"
                    f" or at least {CAPTCHA_MIN_ITEMS} items"
                )
            if value > CAPTCHA_MAX_ITEMS:
                raise ValueError(f"at most {CAPTCHA_MAX_ITEMS} items are supported")
            return value

        @pydantic.field_validator("min_marks_for_captcha")
        @classmethod
        def validate_min_marks_for_captcha(cls, value: int) -> int:
            if value < 1:
                raise ValueError("at least 1 mark is required")
            return value

        @pydantic.field_validator("guest_search_max_pages")
        @classmethod
        def validate_guest_search_max_pages(cls, value: int) -> int:
            # 0 would read as "uncapped" where the limit is applied
            return max(1, value)

        @pydantic.field_validator("language_code")
        @classmethod
        def validate_language_code(cls, value: str) -> str:
            supported = getattr(settings, "SUPPORTED_UI_LANGUAGES", {})
            if supported and value not in supported:
                raise ValueError(
                    f"{value} is not a supported UI language: "
                    f"{', '.join(supported.keys())}"
                )
            return value

        @pydantic.field_validator("email_url")
        @classmethod
        def validate_email_url(cls, value: str) -> str:
            try:
                resolve_email_settings(value, settings.DEBUG)
            except ImproperlyConfigured as exc:
                raise ValueError(str(exc)) from exc
            return value

    @classmethod
    def _env_defaults(cls) -> dict:
        """Read env-var-derived values from django settings as fallbacks.

        set_system() drops values equal to these, so every setting read here
        must stay the untouched env value: _apply_to_settings() must not write
        to it. The settings Django itself consumes (EMAIL_URL, LANGUAGE_CODE)
        keep a separate *_ENV copy for that reason.
        """
        site_info = settings.SITE_INFO
        return {
            # Branding
            "site_name": site_info.get("site_name", ""),
            "site_logo": site_info.get("site_logo", "/s/img/logo.svg"),
            "site_icon": site_info.get("site_icon", "/s/img/icon.png"),
            "user_icon": site_info.get("user_icon", "/s/img/avatar.png"),
            "site_color": site_info.get("site_color", "azure"),
            "site_intro": site_info.get("site_intro", ""),
            "site_head": site_info.get("site_head", ""),
            "site_description": site_info.get(
                "site_description",
                "reviews about book, film, music, podcast and game.",
            ),
            "site_links": {
                item["title"]: item["url"] for item in site_info.get("site_links", [])
            },
            # Access Control
            "invite_only": getattr(settings, "INVITE_ONLY", False),
            "enable_local_only": getattr(settings, "ENABLE_LOCAL_ONLY", False),
            "mastodon_login_whitelist": list(
                getattr(settings, "MASTODON_ALLOWED_SITES", [])
            ),
            "enable_login_mastodon": True,
            "enable_login_bluesky": getattr(settings, "ENABLE_LOGIN_BLUESKY", False),
            "enable_login_threads": getattr(settings, "ENABLE_LOGIN_THREADS", False),
            "email_url": getattr(
                settings, "EMAIL_URL_ENV", getattr(settings, "EMAIL_URL", "")
            )
            or "",
            "email_from": getattr(
                settings,
                "DEFAULT_FROM_EMAIL_ENV",
                getattr(settings, "DEFAULT_FROM_EMAIL", ""),
            )
            or "",
            # Discover
            "min_marks_for_discover": getattr(settings, "MIN_MARKS_FOR_DISCOVER", 1),
            "discover_update_interval": getattr(
                settings, "DISCOVER_UPDATE_INTERVAL", 60
            ),
            "discover_filter_language": getattr(
                settings, "DISCOVER_FILTER_LANGUAGE", False
            ),
            "discover_show_local_only": getattr(
                settings, "DISCOVER_SHOW_LOCAL_ONLY", False
            ),
            "discover_show_popular_posts": getattr(
                settings, "DISCOVER_SHOW_POPULAR_POSTS", False
            ),
            "discover_show_popular_tags": getattr(
                settings, "DISCOVER_SHOW_POPULAR_TAGS", False
            ),
            "discover_show_verified_podcasts": getattr(
                settings, "DISCOVER_SHOW_VERIFIED_PODCASTS", False
            ),
            # Localization
            "preferred_languages": list(
                getattr(settings, "PREFERRED_LANGUAGES", ["en", "zh"])
            ),
            "language_code": getattr(
                settings, "LANGUAGE_CODE_ENV", getattr(settings, "LANGUAGE_CODE", "en")
            )
            or "en",
            # Federation
            "disable_default_relay": getattr(settings, "DISABLE_DEFAULT_RELAY", False),
            "fanout_limit_days": getattr(settings, "FANOUT_LIMIT_DAYS", 9),
            "remote_prune_horizon": getattr(settings, "REMOTE_PRUNE_HORIZON", 92),
            # Search/Catalog
            "search_sites": list(getattr(settings, "SEARCH_SITES", [])),
            "search_peers": list(getattr(settings, "SEARCH_PEERS", [])),
            "hidden_categories": list(getattr(settings, "HIDDEN_CATEGORIES", [])),
            # Catalog genres (in-code defaults; no env var)
            **{
                f"genres_{cat}": list(slugs)
                for cat, slugs in DEFAULT_GENRE_CATEGORIES.items()
            },
            # API Keys - Catalog
            "spotify_api_key": getattr(settings, "SPOTIFY_CREDENTIAL", ""),
            "tmdb_api_key": getattr(settings, "TMDB_API3_KEY", "TESTONLY"),
            "google_api_key": getattr(settings, "GOOGLE_API_KEY", ""),
            "discogs_api_key": getattr(settings, "DISCOGS_API_KEY", "TESTONLY"),
            "igdb_client_id": getattr(settings, "IGDB_CLIENT_ID", "TESTONLY"),
            "igdb_client_secret": getattr(settings, "IGDB_CLIENT_SECRET", ""),
            "bgg_api_token": getattr(settings, "BGG_API_TOKEN", ""),
            "mal_client_id": getattr(settings, "MAL_API_CLIENT_ID", ""),
            # API Keys - Services
            "steam_api_key": getattr(settings, "STEAM_API_KEY", ""),
            "deepl_api_key": getattr(settings, "DEEPL_API_KEY", ""),
            "lt_api_url": getattr(settings, "LT_API_URL", ""),
            "lt_api_key": getattr(settings, "LT_API_KEY", ""),
            "threads_app_id": getattr(settings, "THREADS_APP_ID", ""),
            "threads_app_secret": getattr(settings, "THREADS_APP_SECRET", ""),
            # Notifications
            "discord_webhooks": dict(getattr(settings, "DISCORD_WEBHOOKS", {})),
            # Downloader
            "downloader_proxy_list": list(
                getattr(settings, "DOWNLOADER_PROXY_LIST", [])
            ),
            "downloader_backup_proxy": getattr(settings, "DOWNLOADER_BACKUP_PROXY", ""),
            "downloader_providers": getattr(settings, "DOWNLOADER_PROVIDERS", ""),
            "downloader_scrapfly_key": getattr(settings, "DOWNLOADER_SCRAPFLY_KEY", ""),
            "downloader_decodo_token": getattr(settings, "DOWNLOADER_DECODO_TOKEN", ""),
            "downloader_scraperapi_key": getattr(
                settings, "DOWNLOADER_SCRAPERAPI_KEY", ""
            ),
            "downloader_scrapingbee_key": getattr(
                settings, "DOWNLOADER_SCRAPINGBEE_KEY", ""
            ),
            "downloader_customscraper_url": getattr(
                settings, "DOWNLOADER_CUSTOMSCRAPER_URL", ""
            ),
            "downloader_request_timeout": getattr(
                settings, "DOWNLOADER_REQUEST_TIMEOUT", 90
            ),
            "downloader_cache_timeout": getattr(
                settings, "DOWNLOADER_CACHE_TIMEOUT", 300
            ),
            "downloader_retries": getattr(settings, "DOWNLOADER_RETRIES", 3),
            # Cleanup
            "task_cleanup_days": getattr(settings, "TASK_CLEANUP_DAYS", 28),
            # Advanced / Operational
            "alternative_domains": list(getattr(settings, "ALTERNATIVE_DOMAINS", [])),
            "mastodon_client_scope": getattr(settings, "MASTODON_CLIENT_SCOPE", ""),
            "mastodon_timeout": getattr(settings, "MASTODON_TIMEOUT", 5),
            "disable_cron_jobs": list(getattr(settings, "DISABLE_CRON_JOBS", [])),
            "index_aliases": dict(
                getattr(settings, "INDEX_ALIASES", {"catalog": "catalog2"})
            ),
            # SKIP_MIGRATIONS env is deprecated; kept as a fallback so existing
            # deployments keep working until the admin sets the UI value.
            "skip_migrations": list(getattr(settings, "SKIP_MIGRATIONS", [])),
        }

    @classmethod
    def load_system(cls) -> "SiteConfig.SystemOptions":
        """Load config with fallback: DB values > env values > Pydantic defaults."""
        env_values = cls._env_defaults()
        try:
            obj = cls.objects.filter(pk=1).first()
            if obj and obj.data:
                env_values.update({k: v for k, v in obj.data.items() if v is not None})
        except ProgrammingError, DatabaseError:
            logger.debug("SiteConfig table not available, using env defaults")
        return cls.SystemOptions(**env_values)

    @classmethod
    def set_system(cls, **kwargs: object) -> None:
        """Partial update: merge new values into the JSON blob."""
        with transaction.atomic():
            obj, created = cls.objects.select_for_update().get_or_create(
                pk=1, defaults={"data": {}}
            )
            data = dict(obj.data)
            env_defaults = cls._env_defaults()
            for key, value in kwargs.items():
                if key not in cls.SystemOptions.model_fields:
                    raise KeyError(f"Unknown config key: {key}")
                if value == env_defaults.get(key):
                    data.pop(key, None)
                else:
                    data[key] = value
            # Validate before saving to prevent broken config
            cls.SystemOptions(**{**env_defaults, **data})
            obj.data = data
            obj.save(update_fields=["data"])

    @classmethod
    def reload(cls) -> None:
        """Force-reload config from DB. Used by workers on each job."""
        cls.system = cls.load_system()
        cls._apply_to_settings(cls.system)

    @classmethod
    def ensure_loaded(cls) -> None:
        """Load config if not yet loaded. For use outside request cycle."""
        if not getattr(cls, "system", None):
            cls.reload()

    @staticmethod
    def ready(func):
        """Decorator for RQ jobs that need SiteConfig loaded before execution."""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            SiteConfig.reload()
            return func(*args, **kwargs)

        return wrapper

    @classmethod
    def _apply_to_settings(cls, opts: "SiteConfig.SystemOptions") -> None:
        """Push the config values that Django itself consumes into settings.

        Everything else (branding, timeouts, ...) is read from SiteConfig.system
        at the point of use. Only settings with a separate *_ENV copy may be
        written here, or _env_defaults() would compare against the DB value.
        """
        # Email delivery and login availability
        settings.EMAIL_URL = opts.email_url
        settings.DEFAULT_FROM_EMAIL = opts.email_from
        for key, value in resolve_email_settings(
            opts.email_url, settings.DEBUG
        ).items():
            setattr(settings, key, value)

        # Refresh module-level language caches
        import common.models.lang as lang_module

        previous_languages = list(lang_module.SITE_PREFERRED_LANGUAGES)
        lang_module.SITE_PREFERRED_LANGUAGES[:] = opts.preferred_languages or [
            lang_module.FALLBACK_LANGUAGE
        ]
        lang_module.SITE_DEFAULT_LANGUAGE = lang_module.SITE_PREFERRED_LANGUAGES[0]
        lang_module.SITE_PREFERRED_LOCALES[:] = lang_module.get_preferred_locales()
        languages_changed = previous_languages != lang_module.SITE_PREFERRED_LANGUAGES

        # Default UI language, read live by users.middlewares.LanguageMiddleware.
        # Assigned before the refresh below so consumers derived from it see the
        # new value.
        language_code_changed = settings.LANGUAGE_CODE != opts.language_code
        if language_code_changed:
            settings.LANGUAGE_CODE = opts.language_code
            # trans_real caches the default translation object, built from
            # LANGUAGE_CODE on first use and consulted whenever no language is
            # active (RQ jobs, management commands). Django resets it the same
            # way when LANGUAGE_CODE changes, in django/test/signals.py.
            trans_real._default = None  # ty: ignore[unresolved-attribute]

        if languages_changed or language_code_changed:
            # Rebuilds the choice caches (ordered by the preferred list) and
            # notifies everything derived from either setting. Not free, and it
            # mutates shared objects in place, so only run it on a real change.
            lang_module.refresh_language_caches()

        # Derived values used in many places via settings.SITE_DOMAINS
        settings.SITE_DOMAINS = [settings.SITE_DOMAIN] + opts.alternative_domains
        if not settings.DEBUG:
            # keep host validation in sync with runtime alternative domains,
            # matching the ALLOWED_HOSTS recipe in boofilsic.settings
            settings.ALLOWED_HOSTS = (
                settings.SITE_DOMAINS
                + ["127.0.0.1", "localhost"]
                + (["testserver"] if settings.TESTING else [])
            )
