import json
import os
from collections.abc import Callable
from functools import partial
from typing import Any, ClassVar

import pydantic
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView
from django_jsonform.forms.fields import JSONFormField
from loguru import logger

from catalog.jobs.recommendation import BuildItemSimilarity, BuildUserRecommendations
from common.config import format_config_value
from common.models import SiteConfig
from common.models.site_config import CAPTCHA_MAX_ITEMS


def superuser_required(view_func):
    return user_passes_test(
        lambda u: getattr(u, "is_superuser", False), login_url="/account/login"
    )(view_func)


MANAGE_NAV_SECTIONS = [
    ("branding", _("Branding"), "common:manage_branding"),
    ("discover", _("Discover"), "common:manage_discover"),
    ("recommendations", _("Recommendations"), "common:manage_recommendations"),
    ("access", _("Access"), "common:manage_access"),
    ("federation", _("Federation"), "common:manage_federation"),
    ("catalog", _("Catalog"), "common:manage_catalog"),
    ("api_keys", _("API Keys"), "common:manage_api_keys"),
    ("downloader", _("Downloader"), "common:manage_downloader"),
    ("advanced", _("Advanced"), "common:manage_advanced"),
    ("environment", _("Environment"), "common:manage_environment"),
]

# Environment variables whose value only seeds a SiteConfig field. The field is
# shown on one of the settings pages above, so these are not listed on the
# Environment page. Every other env var read by boofilsic.settings must appear
# in EnvironmentSettings.groups; a test enforces the partition.
ENV_VARS_WITH_SITE_SETTING: dict[str, str] = {
    "NEODB_SITE_NAME": "site_name",
    "NEODB_SITE_LOGO": "site_logo",
    "NEODB_SITE_ICON": "site_icon",
    "NEODB_USER_ICON": "user_icon",
    "NEODB_SITE_COLOR": "site_color",
    "NEODB_SITE_INTRO": "site_intro",
    "NEODB_SITE_HEAD": "site_head",
    "NEODB_SITE_DESCRIPTION": "site_description",
    "NEODB_SITE_LINKS": "site_links",
    "NEODB_ALTERNATIVE_DOMAINS": "alternative_domains",
    "NEODB_PREFERRED_LANGUAGES": "preferred_languages",
    "NEODB_INVITE_ONLY": "invite_only",
    "NEODB_ENABLE_LOCAL_ONLY": "enable_local_only",
    "NEODB_LOGIN_MASTODON_WHITELIST": "mastodon_login_whitelist",
    "NEODB_LOGIN_MASTODON_TIMEOUT": "mastodon_timeout",
    "NEODB_MASTODON_CLIENT_SCOPE": "mastodon_client_scope",
    "NEODB_ENABLE_LOGIN_BLUESKY": "enable_login_bluesky",
    "NEODB_ENABLE_LOGIN_THREADS": "enable_login_threads",
    "NEODB_EMAIL_URL": "email_url",
    "NEODB_EMAIL_FROM": "email_from",
    "NEODB_MIN_MARKS_FOR_DISCOVER": "min_marks_for_discover",
    "NEODB_DISCOVER_UPDATE_INTERVAL": "discover_update_interval",
    "NEODB_DISCOVER_FILTER_LANGUAGE": "discover_filter_language",
    "NEODB_DISCOVER_SHOW_LOCAL_ONLY": "discover_show_local_only",
    "NEODB_DISCOVER_SHOW_POPULAR_POSTS": "discover_show_popular_posts",
    "NEODB_DISCOVER_SHOW_POPULAR_TAGS": "discover_show_popular_tags",
    "NEODB_DISABLE_DEFAULT_RELAY": "disable_default_relay",
    "NEODB_FANOUT_LIMIT_DAYS": "fanout_limit_days",
    "TAKAHE_REMOTE_PRUNE_HORIZON": "remote_prune_horizon",
    "NEODB_SEARCH_SITES": "search_sites",
    "NEODB_SEARCH_PEERS": "search_peers",
    "NEODB_HIDDEN_CATEGORIES": "hidden_categories",
    "SPOTIFY_API_KEY": "spotify_api_key",
    "TMDB_API_V3_KEY": "tmdb_api_key",
    "GOOGLE_API_KEY": "google_api_key",
    "DISCOGS_API_KEY": "discogs_api_key",
    "IGDB_API_CLIENT_ID": "igdb_client_id",
    "IGDB_API_CLIENT_SECRET": "igdb_client_secret",
    "BGG_API_TOKEN": "bgg_api_token",
    "STEAM_API_KEY": "steam_api_key",
    "DEEPL_API_KEY": "deepl_api_key",
    "LT_API_URL": "lt_api_url",
    "LT_API_KEY": "lt_api_key",
    "THREADS_APP_ID": "threads_app_id",
    "THREADS_APP_SECRET": "threads_app_secret",
    "DISCORD_WEBHOOKS": "discord_webhooks",
    "NEODB_DOWNLOADER_PROXY_LIST": "downloader_proxy_list",
    "NEODB_DOWNLOADER_BACKUP_PROXY": "downloader_backup_proxy",
    "NEODB_DOWNLOADER_PROVIDERS": "downloader_providers",
    "NEODB_DOWNLOADER_SCRAPFLY_KEY": "downloader_scrapfly_key",
    "NEODB_DOWNLOADER_DECODO_TOKEN": "downloader_decodo_token",
    "NEODB_DOWNLOADER_SCRAPERAPI_KEY": "downloader_scraperapi_key",
    "NEODB_DOWNLOADER_SCRAPINGBEE_KEY": "downloader_scrapingbee_key",
    "NEODB_DOWNLOADER_CUSTOMSCRAPER_URL": "downloader_customscraper_url",
    "NEODB_DOWNLOADER_REQUEST_TIMEOUT": "downloader_request_timeout",
    "NEODB_DOWNLOADER_CACHE_TIMEOUT": "downloader_cache_timeout",
    "NEODB_DOWNLOADER_RETRIES": "downloader_retries",
    "NEODB_DISABLE_CRON_JOBS": "disable_cron_jobs",
    "INDEX_ALIASES": "index_aliases",
    "SKIP_MIGRATIONS": "skip_migrations",
}


@method_decorator(login_required, name="dispatch")
@method_decorator(superuser_required, name="dispatch")
class SiteConfigSettingsPage(FormView):
    """
    Auto-generates a settings form from ``options`` and ``layout`` dicts,
    backed by ``SiteConfig.SystemOptions`` Pydantic fields.
    """

    template_name = "manage/settings.html"
    section: ClassVar[str]
    options: ClassVar[dict]
    layout: ClassVar[dict]

    def get_form_class(self):
        fields = {}
        for key, details in self.options.items():
            field_info = SiteConfig.SystemOptions.model_fields[key]
            annotation = field_info.annotation
            origin = getattr(annotation, "__origin__", None)

            if annotation is bool:
                form_field = partial(
                    forms.BooleanField,
                    widget=forms.Select(choices=[(True, _("Yes")), (False, _("No"))]),
                )
            elif annotation is str:
                choices = details.get("choices")
                if choices:
                    form_field = partial(
                        forms.CharField,
                        widget=forms.Select(choices=choices),
                    )
                elif details.get("display") == "textarea":
                    form_field = partial(
                        forms.CharField,
                        widget=forms.Textarea(attrs={"rows": 4}),
                    )
                else:
                    form_field = forms.CharField
            elif annotation is int:
                field_kwargs = {}
                for int_kwarg in ("min_value", "max_value", "step_size"):
                    val = details.get(int_kwarg)
                    if val is not None:
                        field_kwargs[int_kwarg] = val
                form_field = partial(forms.IntegerField, **field_kwargs)
            elif annotation is float:
                form_field = partial(
                    forms.FloatField,
                    min_value=details.get("min_value", 0),
                    max_value=details.get("max_value"),
                )
            elif origin is list:
                form_field = partial(
                    forms.CharField,
                    widget=forms.Textarea(attrs={"rows": 3}),
                )
            elif annotation is dict or origin is dict:
                json_schema = details.get("schema")
                if json_schema:
                    fields[key] = JSONFormField(
                        schema=json_schema,
                        label=details["title"],
                        help_text=details.get("help_text", ""),
                        required=False,
                    )
                    continue
                form_field = partial(
                    forms.CharField,
                    widget=forms.Textarea(attrs={"rows": 4}),
                )
            else:
                logger.warning(
                    f"Cannot render settings type {annotation} for key {key}"
                )
                continue

            fields[key] = form_field(
                label=details["title"],
                help_text=details.get("help_text", ""),
                required=False,
            )
        return type("SiteConfigForm", (forms.Form,), fields)

    def get_initial(self):
        config = SiteConfig.load_system()
        initial = {}
        for key in self.options:
            value = getattr(config, key)
            field_info = SiteConfig.SystemOptions.model_fields[key]
            annotation = field_info.annotation
            origin = getattr(annotation, "__origin__", None)
            if origin is list:
                initial[key] = "\n".join(str(v) for v in value) if value else ""
            elif annotation is dict or origin is dict:
                if self.options[key].get("schema"):
                    # JSONFormField handles dicts natively
                    initial[key] = value or {}
                else:
                    # @Key=Value format
                    initial[key] = (
                        "\n".join(f"@{k}={v}" for k, v in value.items())
                        if value
                        else ""
                    )
            else:
                initial[key] = value
        return initial

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["section"] = self.section
        context["fieldsets"] = {}
        for title, fields in self.layout.items():
            context["fieldsets"][title] = [context["form"][field] for field in fields]
        context["nav_sections"] = MANAGE_NAV_SECTIONS
        return context

    def _convert_value(self, key: str, raw_value: object) -> object:
        """Convert form value to the correct type for storage."""
        field_info = SiteConfig.SystemOptions.model_fields[key]
        annotation = field_info.annotation
        origin = getattr(annotation, "__origin__", None)

        if origin is list:
            if not raw_value or not str(raw_value).strip():
                return []
            return [
                line.strip()
                for line in str(raw_value).strip().splitlines()
                if line.strip()
            ]
        elif annotation is dict or origin is dict:
            if self.options[key].get("schema"):
                # JSONFormField already returns a parsed dict
                return raw_value if raw_value else {}
            raw_str = str(raw_value).strip() if raw_value else ""
            if not raw_str:
                return {}
            else:
                result = {}
                for line in raw_str.splitlines():
                    line = line.strip()
                    if line.startswith("@") and "=" in line:
                        k, v = line[1:].split("=", 1)
                        result[k.strip()] = v.strip()
                return result
        return raw_value

    def form_valid(self, form):
        updates = {}
        for key in self.options:
            raw = form.cleaned_data[key]
            try:
                updates[key] = self._convert_value(key, raw)
            except (json.JSONDecodeError, ValueError) as e:
                form.add_error(key, str(e))
                return self.form_invalid(form)
        try:
            SiteConfig.set_system(**updates)
        except pydantic.ValidationError as e:
            logger.warning(f"SiteConfig validation failed: {e}")
            for error in e.errors():
                if error["loc"] and error["loc"][0] in form.fields:
                    form.add_error(str(error["loc"][0]), _("Invalid value."))
            if not form.errors:
                messages.error(
                    self.request,
                    _("Invalid configuration. Please check your input."),
                )
            return self.form_invalid(form)
        SiteConfig.system = SiteConfig.load_system()
        SiteConfig._apply_to_settings(SiteConfig.system)
        messages.success(self.request, _("Settings have been saved."))
        return redirect(".")


class BrandingSettings(SiteConfigSettingsPage):
    section = "branding"

    def form_valid(self, form):
        response = super().form_valid(form)
        # Sync branding changes into Takahe's Config table
        from common.setup import Setup

        try:
            Setup().sync_site_config()
        except Exception:
            pass
        return response

    options = {
        "site_name": {
            "title": _("Site Name"),
        },
        "site_description": {
            "title": _("Site Description"),
            "help_text": _("Short description shown in metadata and about page."),
        },
        "site_logo": {
            "title": _("Site Logo URL"),
            "help_text": _("URL path to the site logo image."),
        },
        "site_icon": {
            "title": _("Site Icon URL"),
            "help_text": _("URL path to the site icon/favicon."),
        },
        "user_icon": {
            "title": _("Default User Avatar URL"),
            "help_text": _("URL path to the default user avatar."),
        },
        "site_color": {
            "title": _("Site Color Theme"),
            "help_text": _("PicoCSS color theme."),
            "choices": [
                ("amber", "Amber"),
                ("azure", "Azure"),
                ("blue", "Blue"),
                ("cyan", "Cyan"),
                ("fuchsia", "Fuchsia"),
                ("green", "Green"),
                ("grey", "Grey"),
                ("indigo", "Indigo"),
                ("jade", "Jade"),
                ("lime", "Lime"),
                ("orange", "Orange"),
                ("pink", "Pink"),
                ("pumpkin", "Pumpkin"),
                ("purple", "Purple"),
                ("red", "Red"),
                ("sand", "Sand"),
                ("slate", "Slate"),
                ("violet", "Violet"),
                ("yellow", "Yellow"),
                ("zinc", "Zinc"),
            ],
        },
        "site_intro": {
            "title": _("Site Introduction"),
            "help_text": _("URL path for the intro/welcome sidebar page."),
        },
        "site_head": {
            "title": _("Custom HTML Head"),
            "help_text": _("Extra HTML injected into the <head> of all pages."),
            "display": "textarea",
        },
        "site_links": {
            "title": _("Footer Links"),
            "help_text": _("Link title mapped to URL."),
            "schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": {"type": "string"},
            },
        },
    }
    layout = {
        _("Branding"): [
            "site_name",
            "site_description",
            "site_logo",
            "site_icon",
            "user_icon",
            "site_color",
        ],
        _("Advanced"): [
            "site_intro",
            "site_head",
            "site_links",
        ],
    }


class DiscoverSettings(SiteConfigSettingsPage):
    section = "discover"
    options = {
        "min_marks_for_discover": {
            "title": _("Minimum Marks for Discover"),
            "help_text": _(
                "Number of marks required for an item to appear in discover."
            ),
            "min_value": 0,
        },
        "discover_update_interval": {
            "title": _("Update Interval (minutes)"),
            "help_text": _("How often to refresh the popular items list."),
            "min_value": 1,
        },
        "discover_filter_language": {
            "title": _("Filter by Preferred Languages"),
            "help_text": _("Only show items with titles in the preferred languages."),
        },
        "discover_show_local_only": {
            "title": _("Show Local Only"),
            "help_text": _(
                "Only show items marked by local users, not the entire network."
            ),
        },
        "discover_show_popular_posts": {
            "title": _("Show Popular Posts"),
            "help_text": _("Show popular public posts instead of recent ones."),
        },
        "discover_show_popular_tags": {
            "title": _("Show Popular Tags"),
            "help_text": _("Show popular public tags on the discover page."),
        },
        "discover_show_verified_podcasts": {
            "title": _("Show Verified Podcasts"),
            "help_text": _(
                "Show a shelf of recent episodes from podcasts with a "
                "verified creator on the discover page."
            ),
        },
    }
    layout = {
        _("Discover"): [
            "min_marks_for_discover",
            "discover_update_interval",
            "discover_filter_language",
            "discover_show_local_only",
            "discover_show_popular_posts",
            "discover_show_popular_tags",
            "discover_show_verified_podcasts",
        ],
    }


class RecommendationSettings(SiteConfigSettingsPage):
    section = "recommendations"

    def form_valid(self, form):
        was_enabled = bool(SiteConfig.system.enable_recommendations)
        response = super().form_valid(form)
        is_enabled = bool(SiteConfig.system.enable_recommendations)
        if was_enabled != is_enabled:
            try:
                if is_enabled:
                    BuildItemSimilarity.reschedule(now=True)
                    BuildUserRecommendations.reschedule(now=True)
                else:
                    BuildItemSimilarity.cancel()
                    BuildUserRecommendations.cancel()
            except Exception as e:
                logger.warning(f"Failed to (re)schedule recommendation jobs: {e}")
        return response

    options = {
        "enable_recommendations": {
            "title": _("Enable Recommendations"),
            "help_text": _(
                "Master switch. When off, all recommendation surfaces are "
                "hidden and the cron jobs are not scheduled."
            ),
        },
        "reco_min_source_marks": {
            "title": _("Source Item Mark Threshold"),
            "help_text": _(
                "Minimum public marks an item needs before similarity rows "
                "are built for it."
            ),
            "min_value": 1,
        },
        "reco_min_target_marks": {
            "title": _("Target Item Mark Threshold"),
            "help_text": _(
                "Minimum public marks an item needs to be eligible as a "
                "recommendation target."
            ),
            "min_value": 1,
        },
        "reco_similarity_top_k": {
            "title": _("Top-K Similar Items per Source"),
            "help_text": _("Number of similar items stored per source item."),
            "min_value": 1,
        },
        "reco_user_top_n": {
            "title": _("Top-N Recommendations per User"),
            "help_text": _("Number of personalised rows stored per user."),
            "min_value": 1,
        },
        "reco_user_idf_dampen": {
            "title": _("Dampen Heavy Shelvers"),
            "help_text": _(
                "Weight each user's contribution by 1/sqrt(n_marks) to "
                "neutralise mega-shelvers in similarity scoring."
            ),
        },
        "reco_user_mark_cap": {
            "title": _("Per-User Mark Cap (training)"),
            "help_text": _(
                "Truncate each user's contribution to their N most recent "
                "marks when building the similarity matrix."
            ),
            "min_value": 2,
        },
        "reco_user_active_days": {
            "title": _("Active-User Window (days)"),
            "help_text": _(
                "Refresh personalised recommendations for users with at "
                "least one public mark in the last N days."
            ),
            "min_value": 1,
        },
        "reco_per_user_seed_cap": {
            "title": _("Per-User Seed Cap (serving)"),
            "help_text": _(
                "Number of recent marks used as seeds when scoring "
                "personalised recommendations for one user."
            ),
            "min_value": 1,
        },
        "reco_lazy_ttl_days": {
            "title": _("Lazy Refresh TTL (days)"),
            "help_text": _(
                "How long cached on-demand recommendations are valid before "
                "the next request triggers a refresh."
            ),
            "min_value": 1,
        },
        "reco_circles_window_days": {
            "title": _("Circles Window (days)"),
            "help_text": _(
                "Look back N days when finding items recently marked by "
                "people the viewer follows."
            ),
            "min_value": 1,
        },
    }
    layout = {
        _("Master Switch"): [
            "enable_recommendations",
        ],
        _("Similarity Builder"): [
            "reco_min_source_marks",
            "reco_min_target_marks",
            "reco_similarity_top_k",
            "reco_user_idf_dampen",
            "reco_user_mark_cap",
        ],
        _("Personalisation"): [
            "reco_user_top_n",
            "reco_user_active_days",
            "reco_per_user_seed_cap",
            "reco_lazy_ttl_days",
            "reco_circles_window_days",
        ],
    }


class AccessSettings(SiteConfigSettingsPage):
    section = "access"
    # annotated so the mix of str/int/list option values does not narrow the
    # inferred type; test_views_manage indexes into this dict
    options: ClassVar[dict] = {
        "invite_only": {
            "title": _("Invite Only"),
            "help_text": _(
                "Require an invite token to register. Invite tokens can be "
                "generated with neodb-manage invite --create."
            ),
        },
        "enable_local_only": {
            "title": _("Enable Local-Only Posting"),
            "help_text": _("Allow users to create posts visible only to local users."),
        },
        "mastodon_login_whitelist": {
            "title": _("Mastodon Login Whitelist"),
            "help_text": _("One domain per line. Leave empty to allow any instance."),
        },
        "registration_captcha_items": {
            "title": _("Registration Captcha Items"),
            "min_value": 0,
            "max_value": CAPTCHA_MAX_ITEMS,
            "help_text": _(
                "Number of item covers a new user must sort into two category "
                "rows before choosing a username. 0 disables the captcha; "
                "5-6 is recommended. The covers carry no title text, so this "
                "cannot be solved with a screen reader: if you enable it, keep "
                "another way in, such as an invite link."
            ),
        },
        "min_marks_for_captcha": {
            "title": _("Registration Captcha Minimum Marks"),
            "min_value": 1,
            "help_text": _(
                "Marks an item needs before the captcha treats it as well known "
                "enough to be recognizable. Categories with too few such items "
                "fall back to any item."
            ),
        },
        "enable_login_mastodon": {
            "title": _("Enable Mastodon Login"),
        },
        "enable_login_bluesky": {
            "title": _("Enable Bluesky Login"),
        },
        "enable_login_threads": {
            "title": _("Enable Threads Login"),
        },
        "email_url": {
            "title": _("Email URL"),
            "help_text": _(
                "Email backend URL for login codes, such as an SMTP or Anymail URL."
            ),
        },
        "email_from": {
            "title": _("Email From"),
            "help_text": _("Sender name and address for outgoing email."),
        },
        "language_code": {
            "title": _("Default Language"),
            "choices": list(settings.LANGUAGES),
            "help_text": _(
                "Interface language for visitors and for users who have not "
                "chosen one themselves."
            ),
        },
        "preferred_languages": {
            "title": _("Preferred Languages"),
            "help_text": _(
                "Language codes, one per line (e.g. en, zh, ja). "
                "First language is the default."
            ),
        },
    }
    layout = {
        _("Access Control"): [
            "invite_only",
            "enable_local_only",
            "mastodon_login_whitelist",
            "registration_captcha_items",
            "min_marks_for_captcha",
        ],
        _("Login Methods"): [
            "enable_login_mastodon",
            "enable_login_bluesky",
            "enable_login_threads",
        ],
        _("Email"): [
            "email_url",
            "email_from",
        ],
        _("Localization"): [
            "language_code",
            "preferred_languages",
        ],
    }


class FederationSettings(SiteConfigSettingsPage):
    section = "federation"
    options = {
        "disable_default_relay": {
            "title": _("Disable Default Relay"),
            "help_text": _(
                "Disable relay.neodb.net federation for sharing "
                "public ratings across instances."
            ),
        },
        "fanout_limit_days": {
            "title": _("Fanout Limit (days)"),
            "help_text": _("Posts older than this many days will not be fanned out."),
            "min_value": 1,
        },
        "remote_prune_horizon": {
            "title": _("Remote Prune Horizon (days)"),
            "help_text": _(
                "Remote profiles inactive for this many days will be pruned."
            ),
            "min_value": 1,
        },
        "search_sites": {
            "title": _("Search Sites"),
            "help_text": _("External search sites to include, one per line."),
        },
        "search_peers": {
            "title": _("Federated Search Peers"),
            "help_text": _("NeoDB peer instances for federated search, one per line."),
        },
        "hidden_categories": {
            "title": _("Hidden Categories"),
            "help_text": _("Category values to hide from the catalog, one per line."),
        },
    }
    layout = {
        _("Federation"): [
            "disable_default_relay",
            "fanout_limit_days",
            "remote_prune_horizon",
        ],
        _("Search"): [
            "search_sites",
            "search_peers",
            "hidden_categories",
        ],
    }


class APIKeysSettings(SiteConfigSettingsPage):
    section = "api_keys"
    options = {
        "spotify_api_key": {
            "title": _("Spotify API Key"),
            "help_text": _("https://developer.spotify.com/"),
        },
        "tmdb_api_key": {
            "title": _("TMDB API Key"),
            "help_text": _("https://developer.themoviedb.org/"),
        },
        "google_api_key": {
            "title": _("Google Books API Key"),
            "help_text": _("https://developers.google.com/books/"),
        },
        "discogs_api_key": {
            "title": _("Discogs API Key"),
            "help_text": _(
                "Personal access token from https://www.discogs.com/settings/developers"
            ),
        },
        "igdb_client_id": {
            "title": _("IGDB Client ID"),
            "help_text": _("https://api-docs.igdb.com/"),
        },
        "igdb_client_secret": {
            "title": _("IGDB Client Secret"),
        },
        "bgg_api_token": {
            "title": _("BoardGameGeek API Token"),
            "help_text": _(
                "Bearer token from https://boardgamegeek.com/applications "
                "(see https://boardgamegeek.com/using_the_xml_api#toc9)."
            ),
        },
        "steam_api_key": {
            "title": _("Steam API Key"),
            "help_text": _(
                "https://steamcommunity.com/dev - fallback key for Steam importer. "
                "Users can provide their own key when importing."
            ),
        },
        "deepl_api_key": {
            "title": _("DeepL API Key"),
            "help_text": _("For translation features."),
        },
        "lt_api_url": {
            "title": _("LibreTranslate API URL"),
        },
        "lt_api_key": {
            "title": _("LibreTranslate API Key"),
        },
        "threads_app_id": {
            "title": _("Threads App ID"),
            "help_text": _("OAuth app ID for Threads login."),
        },
        "threads_app_secret": {
            "title": _("Threads App Secret"),
        },
        "discord_webhooks": {
            "title": _("Discord Webhooks"),
            "help_text": _(
                "Webhook URLs keyed by channel (default, report, audit, suggest, system). "
                "All channels must be Discord forum or media channels (thread mode) "
                "because notifications are posted as threads."
            ),
            "schema": {
                "type": "object",
                "properties": {
                    "default": {"type": "string", "title": "default"},
                    "report": {"type": "string", "title": "report"},
                    "audit": {"type": "string", "title": "audit"},
                    "suggest": {"type": "string", "title": "suggest"},
                    "system": {"type": "string", "title": "system"},
                },
            },
        },
    }
    layout = {
        _("Catalog APIs"): [
            "spotify_api_key",
            "tmdb_api_key",
            "google_api_key",
            "discogs_api_key",
            "igdb_client_id",
            "igdb_client_secret",
            "bgg_api_token",
            "steam_api_key",
        ],
        _("Translation"): [
            "deepl_api_key",
            "lt_api_url",
            "lt_api_key",
        ],
        _("Third-Party Login"): [
            "threads_app_id",
            "threads_app_secret",
        ],
        _("Notifications"): [
            "discord_webhooks",
        ],
    }


class DownloaderSettings(SiteConfigSettingsPage):
    section = "downloader"
    options = {
        "downloader_providers": {
            "title": _("Scraping Providers"),
            "help_text": _("Comma-separated list of providers to try in order."),
        },
        "downloader_proxy_list": {
            "title": _("Proxy List"),
            "help_text": _("One per line, format: http://server?url=__URL__"),
        },
        "downloader_backup_proxy": {
            "title": _("Backup Proxy"),
        },
        "downloader_scrapfly_key": {
            "title": _("Scrapfly API Key"),
        },
        "downloader_decodo_token": {
            "title": _("Decodo Base64 Auth Token"),
        },
        "downloader_scraperapi_key": {
            "title": _("ScraperAPI Key"),
        },
        "downloader_scrapingbee_key": {
            "title": _("ScrapingBee API Key"),
        },
        "downloader_customscraper_url": {
            "title": _("Custom Scraper URL"),
            "help_text": _("URL with __URL__ and __SELECTOR__ placeholders."),
        },
        "downloader_request_timeout": {
            "title": _("Request Timeout (seconds)"),
            "min_value": 1,
        },
        "downloader_cache_timeout": {
            "title": _("Cache Timeout (seconds)"),
            "min_value": 0,
        },
        "downloader_retries": {
            "title": _("Retries"),
            "min_value": 0,
        },
    }
    layout = {
        _("Providers"): [
            "downloader_providers",
            "downloader_proxy_list",
            "downloader_backup_proxy",
        ],
        _("Provider Keys"): [
            "downloader_scrapfly_key",
            "downloader_decodo_token",
            "downloader_scraperapi_key",
            "downloader_scrapingbee_key",
            "downloader_customscraper_url",
        ],
        _("Timeouts"): [
            "downloader_request_timeout",
            "downloader_cache_timeout",
            "downloader_retries",
        ],
    }


class AdvancedSettings(SiteConfigSettingsPage):
    section = "advanced"
    options = {
        "alternative_domains": {
            "title": _("Alternative Domains"),
            "help_text": _("One domain per line."),
        },
        "mastodon_client_scope": {
            "title": _("Mastodon Client Scope"),
            "help_text": _("OAuth scope when creating Mastodon apps."),
        },
        "mastodon_timeout": {
            "title": _("Mastodon API Timeout (seconds)"),
            "help_text": _(
                "Timeout for requests to Mastodon instances and remote "
                "fediverse servers."
            ),
            "min_value": 1,
        },
        "disable_cron_jobs": {
            "title": _("Disable Cron Jobs"),
            "help_text": _("Job names to disable, one per line. Use * to disable all."),
        },
        "index_aliases": {
            "title": _("Index Aliases"),
            "help_text": _("Map index names to their aliases."),
            "schema": {
                "type": "object",
                "properties": {
                    "catalog": {"type": "string", "title": "catalog"},
                },
                "additionalProperties": {"type": "string"},
            },
        },
        "task_cleanup_days": {
            "title": _("Task Cleanup (days)"),
            "help_text": _(
                "Delete import/export tasks and their files after this many days. "
                "Set to 0 to disable cleanup."
            ),
            "min_value": 0,
        },
        "skip_migrations": {
            "title": _("Skip Migration Jobs"),
            "help_text": _(
                "Post-migration job keys to skip, one per line "
                "(e.g. normalize_genre). Checked by the worker at dequeue time; "
                "skipped jobs log a warning and notify the Discord system channel."
            ),
        },
        "atproto_client_jwk": {
            "title": _("ATProto OAuth Client Key"),
            "help_text": _(
                "Private key (JWK) identifying this site to ATProto "
                "authorization servers. Auto-generated on first Bluesky "
                "login; clear to regenerate."
            ),
        },
    }
    layout = {
        _("Domains"): [
            "alternative_domains",
        ],
        _("Operational"): [
            "mastodon_client_scope",
            "mastodon_timeout",
            "disable_cron_jobs",
            "index_aliases",
            "task_cleanup_days",
            "skip_migrations",
            "atproto_client_jwk",
        ],
    }


class CatalogSettings(SiteConfigSettingsPage):
    section = "catalog"
    options = {
        "genres_movie": {
            "title": _("Movie Genres"),
            "help_text": _(
                "Genre codes offered in the Movie edit dropdown, one per line. "
                "Leave empty to use the built-in default."
            ),
        },
        "genres_tv": {
            "title": _("TV Genres"),
            "help_text": _(
                "Genre codes offered in the TV edit dropdown, one per line. "
                "Leave empty to use the built-in default."
            ),
        },
        "genres_music": {
            "title": _("Music Genres"),
            "help_text": _(
                "Genre codes offered in the Music edit dropdown, one per line. "
                "Leave empty to use the built-in default."
            ),
        },
        "genres_game": {
            "title": _("Game Genres"),
            "help_text": _(
                "Genre codes offered in the Game edit dropdown, one per line. "
                "Leave empty to use the built-in default."
            ),
        },
        "genres_podcast": {
            "title": _("Podcast Genres"),
            "help_text": _(
                "Genre codes offered in the Podcast edit dropdown, one per line. "
                "Leave empty to use the built-in default."
            ),
        },
        "genres_performance": {
            "title": _("Performance Genres"),
            "help_text": _(
                "Genre codes offered in the Performance edit dropdown, one per "
                "line. Leave empty to use the built-in default."
            ),
        },
    }
    layout = {
        _("Genres by Category"): [
            "genres_movie",
            "genres_tv",
            "genres_music",
            "genres_game",
            "genres_podcast",
            "genres_performance",
        ],
    }


@method_decorator(login_required, name="dispatch")
@method_decorator(superuser_required, name="dispatch")
class EnvironmentSettings(TemplateView):
    """
    Read-only view of the settings that come from environment variables and
    have no SiteConfig counterpart, so the manage pages together show every
    active setting. Passwords and keys are masked before display.
    """

    template_name = "manage/environment.html"
    section = "environment"
    # group title -> [(env var, getter of the effective value from settings)]
    groups: ClassVar[dict[Any, list[tuple[str, Callable[[], object]]]]] = {
        _("Site"): [
            ("NEODB_SITE_DOMAIN", lambda: settings.SITE_DOMAIN),
            ("NEODB_SITE_URL", lambda: settings.SITE_INFO["site_url"]),
            ("NEODB_DEBUG", lambda: settings.DEBUG),
            ("SSL_ONLY", lambda: settings.SSL_ONLY),
            ("NEODB_TIMEZONE", lambda: settings.TIME_ZONE),
            ("NEODB_LOG_LEVEL", lambda: settings.LOG_LEVEL),
            ("NEODB_ADMIN_HANDLES", lambda: settings.ADMIN_HANDLES),
            ("NEODB_EXTRA_APPS", lambda: settings.EXTRA_APPS),
        ],
        _("Database and Services"): [
            ("NEODB_DB_URL", lambda: settings.DB_URL),
            ("TAKAHE_DB_URL", lambda: settings.TAKAHE_DB_URL),
            (
                "NEODB_DB_CONN_MAX_AGE",
                lambda: settings.DATABASES["default"]["CONN_MAX_AGE"],
            ),
            ("NEODB_REDIS_URL", lambda: settings.REDIS_URL),
            ("NEODB_SEARCH_URL", lambda: settings.SEARCH_URL),
        ],
        _("Media and Files"): [
            ("MEDIA_BACKEND", lambda: settings.MEDIA_BACKEND),
            ("NEODB_MEDIA_ROOT", lambda: settings.MEDIA_ROOT),
            ("NEODB_MEDIA_URL", lambda: settings.MEDIA_URL),
            ("TAKAHE_MEDIA_ROOT", lambda: settings.TAKAHE_MEDIA_ROOT),
            ("TAKAHE_MEDIA_URL", lambda: settings.TAKAHE_MEDIA_URL),
            ("NEODB_STATIC_ROOT", lambda: settings.STATIC_ROOT),
            ("NEODB_DOWNLOADER_SAVE_DIR", lambda: settings.DOWNLOADER_SAVEDIR),
        ],
        _("Monitoring"): [
            ("NEODB_SENTRY_DSN", lambda: settings.SENTRY_DSN),
            ("NEODB_SENTRY_SAMPLE_RATE", lambda: settings.SENTRY_SAMPLE_RATE),
        ],
        _("Security"): [
            ("NEODB_SECRET_KEY", lambda: settings.SECRET_KEY),
        ],
    }

    @classmethod
    def env_var_names(cls) -> set[str]:
        return {name for entries in cls.groups.values() for name, _getter in entries}

    other_title = _("Other Environment Variables")
    other_help = _(
        "Present in the environment of this process but not read by NeoDB "
        "settings. They are used by Docker Compose or by Takahe."
    )

    @staticmethod
    def _is_set(name: str) -> bool:
        # FileAwareEnv also accepts the value from a file named by VAR_FILE
        return name in os.environ or f"{name}_FILE" in os.environ

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["section"] = self.section
        context["nav_sections"] = MANAGE_NAV_SECTIONS
        groups = [
            {
                "title": title,
                "rows": [
                    {
                        "name": name,
                        "value": format_config_value(name, getter()),
                        "is_default": not self._is_set(name),
                    }
                    for name, getter in entries
                ],
            }
            for title, entries in self.groups.items()
        ]
        # Variables this process received that boofilsic.settings never reads,
        # e.g. what docker compose forwards for Takahe. Shown raw, masked.
        known = self.env_var_names() | set(ENV_VARS_WITH_SITE_SETTING)
        other_rows = [
            {"name": name, "value": format_config_value(name, value)}
            for name, value in sorted(os.environ.items())
            if name.startswith(("NEODB_", "TAKAHE_"))
            and name not in known
            and name.removesuffix("_FILE") not in known
        ]
        if other_rows:
            groups.append(
                {"title": self.other_title, "help": self.other_help, "rows": other_rows}
            )
        context["groups"] = groups
        return context


@login_required
@superuser_required
def manage_root(request):
    return redirect("common:manage_branding")
