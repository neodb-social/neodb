import datetime
import markdown_it

import pycountry
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from activities.models import Post
from api import schemas
from core.models import Config
from hatchway import api_view
from takahe.neodb import __version__ as __neodb_version__
from users.models import Domain, Identity


def _build_rules(policy_rules: str | None) -> list[dict]:
    return [
        {"id": str(i + 1), "text": s, "hint": ""}
        for i, s in enumerate(
            [
                s.strip()
                for s in (policy_rules or "").replace("\r", "").split("\n\n")
                if s.strip()
            ]
        )
    ]


@api_view.get
def instance_info_v1(request) -> dict:
    # The stats are expensive to calculate, so don't do it very often
    stats = cache.get("instance_info_stats")
    if stats is None:
        stats = {
            "user_count": Identity.objects.filter(local=True).count(),
            "status_count": Post.objects.filter(local=True).not_hidden().count(),
            "domain_count": Domain.objects.count(),
        }
        cache.set("instance_info_stats", stats, timeout=300)
    admin_identity = (
        Identity.objects.filter(users__admin=True).order_by("created").first()
    )
    rules = _build_rules(request.config.policy_rules)
    return {
        "uri": request.headers.get("host", settings.SETUP.MAIN_DOMAIN),
        "title": request.config.site_name,
        "short_description": "",
        "description": "",
        "email": "",
        "version": f"4.0.4 (compatible; NeoDB {__neodb_version__})",
        "urls": {},
        "stats": stats,
        "thumbnail": request.config.site_banner,
        "languages": ["en"],
        "registrations": (request.config.signup_allowed),
        "approval_required": False,
        "invites_enabled": False,
        "configuration": {
            "accounts": {},
            "statuses": {
                "max_characters": request.config.post_length,
                "max_media_attachments": request.config.max_media_attachments,
                "characters_reserved_per_url": 23,
            },
            "media_attachments": {
                "supported_mime_types": [
                    "image/apng",
                    "image/avif",
                    "image/gif",
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                ],
                "image_size_limit": (1024**2) * 10,
                "image_matrix_limit": 2000 * 2000,
                "video_size_limit": (1024**2) * 10,
                "video_frame_rate_limit": 60,
                "video_matrix_limit": 2000 * 2000,
            },
            "polls": {
                "max_options": 4,
                "max_characters_per_option": 50,
                "min_expiration": 300,
                "max_expiration": 2629746,
            },
        },
        "contact_account": (
            schemas.Account.from_identity(admin_identity) if admin_identity else None
        ),
        "rules": rules,
        "pleroma": {
            "metadata": {
                "features": [
                    "mastodon_api",
                    "quote_posting",
                    "editing",
                    "polls",
                ],
            },
        },
    }


@api_view.get
def instance_info_v2(request) -> dict:
    current_domain = Domain.get_domain(
        request.headers.get("host", settings.SETUP.MAIN_DOMAIN)
    )
    if current_domain is None or not current_domain.local:
        current_domain = Domain.get_domain(settings.SETUP.MAIN_DOMAIN)
    if current_domain is None:
        raise ValueError("No domain set up for MAIN_DOMAIN")
    admin_identity = (
        Identity.objects.filter(users__admin=True).order_by("created").first()
    )
    rules = _build_rules(request.config.policy_rules)
    return {
        "domain": current_domain.domain,
        "title": Config.system.site_name,
        "version": f"4.0.4 (compatible; NeoDB {__neodb_version__})",
        "source_url": "https://github.com/jointakahe/takahe",
        "description": "",
        "usage": {
            "users": {
                "active_month": Identity.objects.filter(local=True).count(),
            }
        },
        "thumbnail": {
            "url": Config.system.site_banner,
        },
        "languages": ["en"],
        "configuration": {
            "urls": {},
            "vapid": {
                "public_key": settings.SETUP.VAPID_PUBLIC_KEY,
            },
            "accounts": {"max_featured_tags": 10, "max_pinned_statuses": 5},
            "statuses": {
                "max_characters": Config.system.post_length,
                "max_media_attachments": Config.system.max_media_attachments,
                "characters_reserved_per_url": 23,
            },
            "media_attachments": {
                "supported_mime_types": [
                    "image/apng",
                    "image/avif",
                    "image/gif",
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                ],
                "image_size_limit": (1024**2) * 10,
                "image_matrix_limit": 2000 * 2000,
                "video_size_limit": (1024**2) * 10,
                "video_frame_rate_limit": 60,
                "video_matrix_limit": 2000 * 2000,
            },
            "polls": {
                "max_options": 4,
                "max_characters_per_option": 50,
                "min_expiration": 300,
                "max_expiration": 2629746,
            },
            "translation": {"enabled": False},
        },
        "registrations": {
            "enabled": Config.system.signup_allowed,
            "approval_required": False,
            "message": None,
            "url": None,
        },
        "contact": {
            "email": "",
            "account": (
                schemas.Account.from_identity(admin_identity)
                if admin_identity
                else None
            ),
        },
        "rules": rules,
        "pleroma": {
            "metadata": {
                "features": [
                    "mastodon_api",
                    "quote_posting",
                    "editing",
                    "polls",
                ],
            },
        },
    }


@api_view.get
def peers(request) -> list[str]:
    return list(
        Domain.objects.filter(local=False, blocked=False).values_list(
            "domain", flat=True
        )
    )


@api_view.get
def activity(request) -> list:
    """
    Weekly activity endpoint
    """
    # The stats are expensive to calculate, so don't do it very often
    stats = cache.get("instance_activity_stats")
    if stats is None:
        stats = []
        # Work out our most recent week start
        now = timezone.now()
        week_start = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - datetime.timedelta(now.weekday())
        for i in range(12):
            week_end = week_start + datetime.timedelta(days=7)
            stats.append(
                {
                    "week": int(week_start.timestamp()),
                    "statuses": Post.objects.filter(
                        local=True, created__gte=week_start, created__lt=week_end
                    ).count(),
                    # TODO: Populate when we have identity activity tracking
                    "logins": 0,
                    "registrations": Identity.objects.filter(
                        local=True, created__gte=week_start, created__lt=week_end
                    ).count(),
                }
            )
            week_start -= datetime.timedelta(days=7)
        cache.set("instance_activity_stats", stats, timeout=300)
    return stats


@api_view.get
def languages(request) -> list:
    languages = cache.get("instance_languages")
    if languages is None:
        languages = [
            {"code": lang.alpha_2, "name": lang.name.split("(")[0]}
            for lang in pycountry.languages
            if hasattr(lang, "alpha_2")
        ]
        cache.set("instance_languages", languages, timeout=3600)
    return languages


@api_view.get
def extended_description(request) -> dict:
    txt = markdown_it.MarkdownIt().render(request.config.site_about)
    return {"content": txt}


@api_view.get
def rules(request) -> list[dict]:
    return _build_rules(request.config.policy_rules)
