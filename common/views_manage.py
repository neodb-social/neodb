import json
from functools import partial
from typing import ClassVar

import pydantic
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView
from django_jsonform.forms.fields import JSONFormField
from loguru import logger

from common.models import SiteConfig


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_superuser, login_url="/account/login")(
        view_func
    )


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
                    widget=forms.Select(
                        choices=[(True, _("Enabled")), (False, _("Disabled"))]
                    ),
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["section"] = self.section
        context["fieldsets"] = {}
        for title, fields in self.layout.items():
            context["fieldsets"][title] = [context["form"][field] for field in fields]
        context["nav_sections"] = [
            ("branding", _("Branding"), "common:manage_branding"),
            ("discover", _("Discover"), "common:manage_discover"),
            ("access", _("Access"), "common:manage_access"),
            ("federation", _("Federation"), "common:manage_federation"),
            ("api_keys", _("API Keys"), "common:manage_api_keys"),
            ("downloader", _("Downloader"), "common:manage_downloader"),
            ("advanced", _("Advanced"), "common:manage_advanced"),
        ]
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
    }
    layout = {
        _("Discover"): [
            "min_marks_for_discover",
            "discover_update_interval",
            "discover_filter_language",
            "discover_show_local_only",
            "discover_show_popular_posts",
            "discover_show_popular_tags",
        ],
    }


class AccessSettings(SiteConfigSettingsPage):
    section = "access"
    options = {
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
        "enable_login_bluesky": {
            "title": _("Enable Bluesky Login"),
        },
        "enable_login_threads": {
            "title": _("Enable Threads Login"),
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
        ],
        _("Login Methods"): [
            "enable_login_bluesky",
            "enable_login_threads",
        ],
        _("Localization"): [
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
        "sentry_dsn": {
            "title": _("Sentry DSN"),
            "help_text": _("Requires restart to take effect."),
        },
        "sentry_sample_rate": {
            "title": _("Sentry Sample Rate"),
            "help_text": _("0.0 to 1.0. Requires restart to take effect."),
            "min_value": 0,
            "max_value": 1,
        },
        "discord_webhooks": {
            "title": _("Discord Webhooks"),
            "help_text": _(
                "Webhook URLs keyed by channel (default, report, audit, suggest)."
            ),
            "schema": {
                "type": "object",
                "properties": {
                    "default": {"type": "string", "title": "default"},
                    "report": {"type": "string", "title": "report"},
                    "audit": {"type": "string", "title": "audit"},
                    "suggest": {"type": "string", "title": "suggest"},
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
        _("Monitoring"): [
            "sentry_dsn",
            "sentry_sample_rate",
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
    }
    layout = {
        _("Domains"): [
            "alternative_domains",
        ],
        _("Operational"): [
            "mastodon_client_scope",
            "disable_cron_jobs",
            "index_aliases",
            "task_cleanup_days",
        ],
    }


@login_required
@superuser_required
def manage_root(request):
    return redirect("common:manage_branding")
