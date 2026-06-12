from django import template
from django.template.defaultfilters import stringfilter

register = template.Library()


@register.filter(is_safe=True)
@stringfilter
def strip_scheme(value):
    """Strip the `https://.../` part of urls"""
    value = value.removeprefix("https://").removeprefix("http://")

    if value.endswith("/"):
        value = value[0:-1]
    return value
