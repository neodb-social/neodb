from django import template
from django.template.defaultfilters import stringfilter

register = template.Library()


@register.filter
@stringfilter
def http_href(value):
    """A stored link for an href: only http(s) URLs, anything else is dropped.
    Scraped and peer-supplied link fields never go through form validation."""
    return value if value.startswith(("http://", "https://")) else ""


@register.filter(is_safe=True)
@stringfilter
def strip_scheme(value):
    """Strip the `https://.../` part of urls"""
    value = value.removeprefix("https://").removeprefix("http://")

    if value.endswith("/"):
        value = value[0:-1]
    return value
