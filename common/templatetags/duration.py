from django import template
from django.template.defaultfilters import stringfilter
from django.utils.text import Truncator
from django.utils.safestring import mark_safe
from catalog.common.models import ItemCategory, item_categories
from catalog.search.views import visible_categories as _visible_categories

register = template.Library()


@register.simple_tag(takes_context=True)
def visible_categories(context):
    return _visible_categories(context["request"])


@register.simple_tag
def all_categories():
    return item_categories()


@register.filter(is_safe=True)
@stringfilter
def duration_format(value, unit):
    duration = int(value or 0) // int(unit or 1)
    h = duration // 3600
    m = duration % 3600 // 60
    s = duration % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"
    # return (f"{h}小时 " if h else "") + (f"{m}分钟" if m else "")


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


@register.filter
def make_range(number):
    return range(1, number + 1)
