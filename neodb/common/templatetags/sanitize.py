import nh3
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def sanitize(value: str | None, tags: str = "") -> str:
    """Strip unsafe HTML, keeping only `tags` (comma separated, nh3 defaults if omitted).

    No `@stringfilter`: it would turn a null value into the literal "None".
    """
    if not value:
        return ""
    allowed = {t.strip() for t in tags.split(",") if t.strip()} if tags else None
    return mark_safe(nh3.clean(str(value), tags=allowed))
