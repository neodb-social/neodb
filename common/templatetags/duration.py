from datetime import date, datetime, timedelta, timezone

from django import template
from django.conf import settings
from django.template.defaultfilters import stringfilter
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from catalog.common.models import item_categories
from catalog.search.views import visible_categories as _visible_categories

register = template.Library()


@register.simple_tag(takes_context=True)
def visible_categories(context):
    return _visible_categories(context["request"])


@register.simple_tag
def all_categories():
    return item_categories()


@register.simple_tag
def all_languages():
    return settings.LANGUAGES


@register.filter(is_safe=True)
@stringfilter
def duration_format(value, unit):
    try:
        duration = int(value or 0) // int(unit or 1)
        h = duration // 3600
        m = duration % 3600 // 60
        s = duration % 60
        return f"{h}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"
    except Exception:
        return f"{value} (format error)"


@register.filter(is_safe=True)
def naturaldelta(v: datetime | None):
    if not v:
        return ""
    d = int(datetime.timestamp(datetime.now()) - datetime.timestamp(v))
    if d < 60:
        return _("just now")
    if d < 3600:
        return f"{d//60}m"
    if d < 38400:
        return f"{d//3600}h"
    if d < 38400 * 14:
        return f"{d//38400}d"
    if d < 38400 * 56:
        return f"{d//38400//7}w"
    if d < 38400 * 30 * 18:
        return f"{d//38400//30}mo"
    return f"{d//38400//365}yr"


@register.filter(is_safe=True)
@stringfilter
def rating_star(value):
    try:
        v = float(value or 0)
    except ValueError:
        v = 0
    pct = round(10 * v)
    if pct > 100:
        pct = 100
    elif pct < 0:
        pct = 0
    html = f'<span class="rating-star" data-rating="{v}"><div style="width:{pct}%;"></div></span>'
    return mark_safe(html)


@register.filter()
@stringfilter
def relative_uri(value: str) -> str:
    return str(value).replace(settings.SITE_INFO["site_url"], "")


@register.filter
def make_range(number):
    return range(1, number + 1)
