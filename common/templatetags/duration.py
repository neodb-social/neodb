from django import template
from django.template.defaultfilters import stringfilter
from django.utils.text import Truncator

register = template.Library()


@register.filter(is_safe=True)
@stringfilter
def duration_format(value, unit):
    duration = int(value or 0) // int(unit or 1)
    h = duration // 3600
    m = duration % 3600 // 60
    s = duration % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"
    # return (f"{h}小时 " if h else "") + (f"{m}分钟" if m else "")
