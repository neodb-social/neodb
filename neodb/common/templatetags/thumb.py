from django import template
from django.conf import settings
from easy_thumbnails.templatetags.thumbnail import thumbnail_url

from common.models.misc import MISSING_COVER

register = template.Library()


@register.filter
def thumb(source, alias):
    """
    This filter modifies that from `easy_thumbnails` so that
    it can neglect .svg file.
    """
    if not source or source == MISSING_COVER:
        return getattr(
            getattr(source, "instance", None),
            "display_cover_image_url",
            settings.SITE_INFO["default_cover_url"],
        )
    try:
        if source.url.endswith(".svg"):
            return source.url
        else:
            return thumbnail_url(source, alias)
    except Exception:
        return ""
