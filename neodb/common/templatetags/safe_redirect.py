from django import template

from common.validators import is_safe_url

register = template.Library()


@register.filter
def safe_next_url(url: str | None, default: str = "/") -> str:
    """Return URL if safe for redirect, otherwise return default."""
    if url and is_safe_url(url):
        return url
    return default
