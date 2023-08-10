from django import template
from django.template.defaultfilters import stringfilter
from django.utils.text import Truncator

register = template.Library()


@register.filter(is_safe=True)
@stringfilter
def truncate(value, arg):
    """Truncate a string after `arg` number of characters."""
    try:
        length = int(arg)
    except ValueError:  # Invalid literal for int().
        return value  # Fail silently.
    return Truncator(value).chars(length, truncate="...")
