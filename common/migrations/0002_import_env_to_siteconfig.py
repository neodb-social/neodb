"""
One-time data migration: import env-derived settings into SiteConfig.

Reads current values from django.conf.settings (which were populated from
.env at startup) and stores any non-default values in the SiteConfig
singleton row.  Idempotent: skips if the row already exists.
"""

from django.db import migrations

# Hardcoded Pydantic defaults -- must match SiteConfig.SystemOptions defaults
# at the time this migration was written.
_PYDANTIC_DEFAULTS = {
    "site_name": "",
    "site_logo": "/s/img/logo.svg",
    "site_icon": "/s/img/icon.png",
    "user_icon": "/s/img/avatar.svg",
    "site_color": "azure",
    "site_intro": "",
    "site_head": "",
    "site_description": "reviews about book, film, music, podcast and game.",
    "site_links": {},
    "invite_only": False,
    "enable_local_only": False,
    "mastodon_login_whitelist": [],
    "enable_login_bluesky": False,
    "enable_login_threads": False,
    "min_marks_for_discover": 1,
    "discover_update_interval": 60,
    "discover_filter_language": False,
    "discover_show_local_only": False,
    "discover_show_popular_posts": False,
    "discover_show_popular_tags": False,
    "preferred_languages": ["en", "zh"],
    "disable_default_relay": False,
    "fanout_limit_days": 9,
    "remote_prune_horizon": 92,
    "search_sites": [],
    "search_peers": [],
    "hidden_categories": [],
}


def import_env(apps, schema_editor):
    SiteConfig = apps.get_model("common", "SiteConfig")
    if SiteConfig.objects.filter(pk=1).exists():
        return  # already imported

    from django.conf import settings

    env_values = {
        "site_name": getattr(settings, "SITE_INFO", {}).get("site_name", ""),
        "site_logo": getattr(settings, "SITE_INFO", {}).get(
            "site_logo", "/s/img/logo.svg"
        ),
        "site_icon": getattr(settings, "SITE_INFO", {}).get(
            "site_icon", "/s/img/icon.png"
        ),
        "user_icon": getattr(settings, "SITE_INFO", {}).get(
            "user_icon", "/s/img/avatar.svg"
        ),
        "site_color": getattr(settings, "SITE_INFO", {}).get("site_color", "azure"),
        "site_intro": getattr(settings, "SITE_INFO", {}).get("site_intro", ""),
        "site_head": getattr(settings, "SITE_INFO", {}).get("site_head", ""),
        "site_description": getattr(settings, "SITE_INFO", {}).get(
            "site_description",
            "reviews about book, film, music, podcast and game.",
        ),
        "site_links": {
            item["title"]: item["url"]
            for item in getattr(settings, "SITE_INFO", {}).get("site_links", [])
        },
        "invite_only": getattr(settings, "INVITE_ONLY", False),
        "enable_local_only": getattr(settings, "ENABLE_LOCAL_ONLY", False),
        "mastodon_login_whitelist": list(
            getattr(settings, "MASTODON_ALLOWED_SITES", [])
        ),
        "enable_login_bluesky": getattr(settings, "ENABLE_LOGIN_BLUESKY", False),
        "enable_login_threads": getattr(settings, "ENABLE_LOGIN_THREADS", False),
        "min_marks_for_discover": getattr(settings, "MIN_MARKS_FOR_DISCOVER", 1),
        "discover_update_interval": getattr(settings, "DISCOVER_UPDATE_INTERVAL", 60),
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
        "preferred_languages": list(
            getattr(settings, "PREFERRED_LANGUAGES", ["en", "zh"])
        ),
        "disable_default_relay": getattr(settings, "DISABLE_DEFAULT_RELAY", False),
        "fanout_limit_days": getattr(settings, "FANOUT_LIMIT_DAYS", 9),
        "remote_prune_horizon": getattr(settings, "REMOTE_PRUNE_HORIZON", 92),
        "search_sites": list(getattr(settings, "SEARCH_SITES", [])),
        "search_peers": list(getattr(settings, "SEARCH_PEERS", [])),
        "hidden_categories": list(getattr(settings, "HIDDEN_CATEGORIES", [])),
    }

    # Only store values that differ from Pydantic defaults
    data = {}
    for key, value in env_values.items():
        if value != _PYDANTIC_DEFAULTS.get(key):
            data[key] = value

    if data:
        SiteConfig.objects.create(pk=1, data=data)


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(import_env, migrations.RunPython.noop),
    ]
