from django import template
from easy_thumbnails.templatetags.thumbnail import thumbnail_url

register = template.Library()


@register.filter
def thumb(source, alias):
    """
    This filter modifies that from `easy_thumbnails` so that
    it can neglect .svg file.
    """
    try:
        if source.url.endswith(".svg"):
            return source.url
        else:
            return thumbnail_url(source, alias)
    except Exception:
        return ""
