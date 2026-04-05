from typing import ClassVar

import pydantic
from django.db import models
from django.db.utils import DatabaseError, ProgrammingError
from loguru import logger


class SiteConfig(models.Model):
    """
    Singleton model storing site-wide configuration as a single JSON blob.

    Only the row with pk=1 is used. Access the current config via the
    class-level ``SiteConfig.system`` attribute, which is refreshed
    periodically by ``SiteConfigMiddleware``.
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
        user_icon: str = "/s/img/avatar.svg"
        site_color: str = "azure"
        site_intro: str = ""
        site_head: str = ""
        site_description: str = "reviews about book, film, music, podcast and game."
        site_links: dict = {}

        # Access Control
        invite_only: bool = False
        enable_local_only: bool = False
        mastodon_login_whitelist: list[str] = []

        # Auth Options
        enable_login_bluesky: bool = False
        enable_login_threads: bool = False

        # Discover
        min_marks_for_discover: int = 1
        discover_update_interval: int = 60
        discover_filter_language: bool = False
        discover_show_local_only: bool = False
        discover_show_popular_posts: bool = False
        discover_show_popular_tags: bool = False

        # Localization
        preferred_languages: list[str] = ["en", "zh"]

        # Federation
        disable_default_relay: bool = False
        fanout_limit_days: int = 9
        remote_prune_horizon: int = 92

        # Search/Catalog
        search_sites: list[str] = []
        search_peers: list[str] = []
        hidden_categories: list[str] = []

        # API Keys - Catalog
        spotify_api_key: str = ""
        tmdb_api_key: str = "TESTONLY"
        google_api_key: str = ""
        discogs_api_key: str = "TESTONLY"
        igdb_client_id: str = "TESTONLY"
        igdb_client_secret: str = ""

        # API Keys - Services
        steam_api_key: str = ""
        deepl_api_key: str = ""
        lt_api_url: str = ""
        lt_api_key: str = ""
        threads_app_id: str = ""
        threads_app_secret: str = ""

        # Monitoring & Notifications
        sentry_dsn: str = ""
        sentry_sample_rate: float = 0.0
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

        # Advanced / Operational
        alternative_domains: list[str] = []
        mastodon_client_scope: str = (
            "read:accounts read:follows read:search"
            " read:blocks read:mutes"
            " write:statuses write:media"
        )
        log_level: str = ""
        disable_cron_jobs: list[str] = []
        index_aliases: dict = {"catalog": "catalog2"}
        skip_migrations: list[str] = []

    @classmethod
    def _env_defaults(cls) -> dict:
        """Read current env-var-derived values from django settings as fallbacks."""
        from django.conf import settings

        return {
            # Branding
            "site_name": settings.SITE_INFO.get("site_name", ""),
            "site_logo": settings.SITE_INFO.get("site_logo", "/s/img/logo.svg"),
            "site_icon": settings.SITE_INFO.get("site_icon", "/s/img/icon.png"),
            "user_icon": settings.SITE_INFO.get("user_icon", "/s/img/avatar.svg"),
            "site_color": settings.SITE_INFO.get("site_color", "azure"),
            "site_intro": settings.SITE_INFO.get("site_intro", ""),
            "site_head": settings.SITE_INFO.get("site_head", ""),
            "site_description": settings.SITE_INFO.get(
                "site_description",
                "reviews about book, film, music, podcast and game.",
            ),
            "site_links": {
                item["title"]: item["url"]
                for item in settings.SITE_INFO.get("site_links", [])
            },
            # Access Control
            "invite_only": getattr(settings, "INVITE_ONLY", False),
            "enable_local_only": getattr(settings, "ENABLE_LOCAL_ONLY", False),
            "mastodon_login_whitelist": list(
                getattr(settings, "MASTODON_ALLOWED_SITES", [])
            ),
            "enable_login_bluesky": getattr(settings, "ENABLE_LOGIN_BLUESKY", False),
            "enable_login_threads": getattr(settings, "ENABLE_LOGIN_THREADS", False),
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
            # Localization
            "preferred_languages": list(
                getattr(settings, "PREFERRED_LANGUAGES", ["en", "zh"])
            ),
            # Federation
            "disable_default_relay": getattr(settings, "DISABLE_DEFAULT_RELAY", False),
            "fanout_limit_days": getattr(settings, "FANOUT_LIMIT_DAYS", 9),
            "remote_prune_horizon": getattr(settings, "REMOTE_PRUNE_HORIZON", 92),
            # Search/Catalog
            "search_sites": list(getattr(settings, "SEARCH_SITES", [])),
            "search_peers": list(getattr(settings, "SEARCH_PEERS", [])),
            "hidden_categories": list(getattr(settings, "HIDDEN_CATEGORIES", [])),
            # API Keys - Catalog
            "spotify_api_key": getattr(settings, "SPOTIFY_CREDENTIAL", ""),
            "tmdb_api_key": getattr(settings, "TMDB_API3_KEY", "TESTONLY"),
            "google_api_key": getattr(settings, "GOOGLE_API_KEY", ""),
            "discogs_api_key": getattr(settings, "DISCOGS_API_KEY", "TESTONLY"),
            "igdb_client_id": getattr(settings, "IGDB_CLIENT_ID", "TESTONLY"),
            "igdb_client_secret": getattr(settings, "IGDB_CLIENT_SECRET", ""),
            # API Keys - Services
            "steam_api_key": getattr(settings, "STEAM_API_KEY", ""),
            "deepl_api_key": getattr(settings, "DEEPL_API_KEY", ""),
            "lt_api_url": getattr(settings, "LT_API_URL", ""),
            "lt_api_key": getattr(settings, "LT_API_KEY", ""),
            "threads_app_id": getattr(settings, "THREADS_APP_ID", ""),
            "threads_app_secret": getattr(settings, "THREADS_APP_SECRET", ""),
            # Monitoring & Notifications
            "sentry_dsn": getattr(settings, "_SENTRY_DSN", ""),
            "sentry_sample_rate": getattr(settings, "_SENTRY_SAMPLE_RATE", 0.0),
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
            # Advanced / Operational
            "alternative_domains": list(getattr(settings, "ALTERNATIVE_DOMAINS", [])),
            "mastodon_client_scope": getattr(settings, "MASTODON_CLIENT_SCOPE", ""),
            "log_level": getattr(settings, "LOG_LEVEL", ""),
            "disable_cron_jobs": list(getattr(settings, "DISABLE_CRON_JOBS", [])),
            "index_aliases": dict(
                getattr(settings, "INDEX_ALIASES", {"catalog": "catalog2"})
            ),
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
        except (ProgrammingError, DatabaseError):
            logger.debug("SiteConfig table not available, using env defaults")
        return cls.SystemOptions(**env_values)

    @classmethod
    def set_system(cls, **kwargs: object) -> None:
        """Partial update: merge new values into the JSON blob."""
        obj, created = cls.objects.get_or_create(pk=1, defaults={"data": {}})
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
    def ensure_loaded(cls) -> None:
        """Ensure config is loaded, for use outside request cycle."""
        if not getattr(cls, "system", None):
            cls.system = cls.load_system()
            cls._apply_to_settings(cls.system)

    @classmethod
    def _apply_to_settings(cls, opts: "SiteConfig.SystemOptions") -> None:
        """Write config values back to django.conf.settings for backward compat."""
        from django.conf import settings

        # Branding -> SITE_INFO
        si = settings.SITE_INFO
        si["site_name"] = opts.site_name
        si["site_logo"] = opts.site_logo
        si["site_icon"] = opts.site_icon
        si["user_icon"] = opts.user_icon
        si["site_color"] = opts.site_color
        si["site_intro"] = opts.site_intro
        si["site_head"] = opts.site_head
        si["site_description"] = opts.site_description
        si["site_links"] = [{"title": k, "url": v} for k, v in opts.site_links.items()]
        si["enable_login_atproto"] = opts.enable_login_bluesky
        si["translate_enabled"] = bool(opts.deepl_api_key) or bool(opts.lt_api_url)

        # Access Control
        settings.INVITE_ONLY = opts.invite_only
        settings.ENABLE_LOCAL_ONLY = opts.enable_local_only
        settings.ENABLE_LOGIN_BLUESKY = opts.enable_login_bluesky
        settings.ENABLE_LOGIN_THREADS = opts.enable_login_threads
        settings.MASTODON_ALLOWED_SITES = opts.mastodon_login_whitelist
        settings.MASTODON_ALLOW_ANY_SITE = len(opts.mastodon_login_whitelist) == 0

        # Discover
        settings.MIN_MARKS_FOR_DISCOVER = opts.min_marks_for_discover
        settings.DISCOVER_UPDATE_INTERVAL = opts.discover_update_interval
        settings.DISCOVER_FILTER_LANGUAGE = opts.discover_filter_language
        settings.DISCOVER_SHOW_LOCAL_ONLY = opts.discover_show_local_only
        settings.DISCOVER_SHOW_POPULAR_POSTS = opts.discover_show_popular_posts
        settings.DISCOVER_SHOW_POPULAR_TAGS = opts.discover_show_popular_tags

        # Localization
        settings.PREFERRED_LANGUAGES = opts.preferred_languages
        # Refresh module-level language caches
        import common.models.lang as lang_module

        lang_module.SITE_PREFERRED_LANGUAGES = opts.preferred_languages or [
            lang_module.FALLBACK_LANGUAGE
        ]
        lang_module.SITE_DEFAULT_LANGUAGE = lang_module.SITE_PREFERRED_LANGUAGES[0]
        lang_module.SITE_PREFERRED_LOCALES = lang_module.get_preferred_locales()

        # Federation
        settings.DISABLE_DEFAULT_RELAY = opts.disable_default_relay
        settings.FANOUT_LIMIT_DAYS = opts.fanout_limit_days
        settings.REMOTE_PRUNE_HORIZON = opts.remote_prune_horizon

        # Search/Catalog
        settings.SEARCH_SITES = opts.search_sites
        settings.SEARCH_PEERS = opts.search_peers
        settings.HIDDEN_CATEGORIES = opts.hidden_categories

        # API Keys
        settings.SPOTIFY_CREDENTIAL = opts.spotify_api_key
        settings.TMDB_API3_KEY = opts.tmdb_api_key
        settings.GOOGLE_API_KEY = opts.google_api_key
        settings.DISCOGS_API_KEY = opts.discogs_api_key
        settings.IGDB_CLIENT_ID = opts.igdb_client_id
        settings.IGDB_CLIENT_SECRET = opts.igdb_client_secret
        settings.STEAM_API_KEY = opts.steam_api_key
        settings.DEEPL_API_KEY = opts.deepl_api_key
        settings.LT_API_URL = opts.lt_api_url
        settings.LT_API_KEY = opts.lt_api_key
        settings.THREADS_APP_ID = opts.threads_app_id
        settings.THREADS_APP_SECRET = opts.threads_app_secret
        settings.DISCORD_WEBHOOKS = opts.discord_webhooks

        # Downloader
        settings.DOWNLOADER_PROXY_LIST = opts.downloader_proxy_list
        settings.DOWNLOADER_BACKUP_PROXY = opts.downloader_backup_proxy
        settings.DOWNLOADER_PROVIDERS = opts.downloader_providers
        settings.DOWNLOADER_SCRAPFLY_KEY = opts.downloader_scrapfly_key
        settings.DOWNLOADER_DECODO_TOKEN = opts.downloader_decodo_token
        settings.DOWNLOADER_SCRAPERAPI_KEY = opts.downloader_scraperapi_key
        settings.DOWNLOADER_SCRAPINGBEE_KEY = opts.downloader_scrapingbee_key
        settings.DOWNLOADER_CUSTOMSCRAPER_URL = opts.downloader_customscraper_url
        settings.DOWNLOADER_REQUEST_TIMEOUT = opts.downloader_request_timeout
        settings.DOWNLOADER_CACHE_TIMEOUT = opts.downloader_cache_timeout
        settings.DOWNLOADER_RETRIES = opts.downloader_retries

        # Advanced / Operational
        settings.ALTERNATIVE_DOMAINS = opts.alternative_domains
        settings.SITE_DOMAINS = [settings.SITE_DOMAIN] + opts.alternative_domains
        settings.MASTODON_CLIENT_SCOPE = opts.mastodon_client_scope
        settings.DISABLE_CRON_JOBS = opts.disable_cron_jobs
        settings.INDEX_ALIASES = opts.index_aliases
        settings.SKIP_MIGRATIONS = opts.skip_migrations
        if opts.log_level:
            settings.LOG_LEVEL = opts.log_level
