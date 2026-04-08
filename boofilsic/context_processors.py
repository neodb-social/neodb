from django.conf import settings

from common.models.site_config import SiteConfig


def site_info(request):
    context = dict(
        settings.SITE_INFO
    )  # static fields (site_url, site_domain, cdn_url, etc.)
    if hasattr(SiteConfig, "system"):
        opts = SiteConfig.system
        context["site_name"] = opts.site_name
        context["site_logo"] = opts.site_logo
        context["site_icon"] = opts.site_icon
        context["user_icon"] = opts.user_icon
        context["site_color"] = opts.site_color
        context["site_intro"] = opts.site_intro
        context["site_head"] = opts.site_head
        context["site_description"] = opts.site_description
        context["site_links"] = [
            {"title": k, "url": v} for k, v in opts.site_links.items()
        ]
        context["enable_login_atproto"] = opts.enable_login_bluesky
        context["translate_enabled"] = bool(opts.deepl_api_key) or bool(opts.lt_api_url)
    context["debug_enabled"] = settings.DEBUG
    if hasattr(request, "user") and request.user.is_authenticated:
        context["has_passkeys"] = request.user.webauthn_credentials.exists()
    return context
