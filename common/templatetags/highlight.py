from django import template
from django.utils.safestring import mark_safe
from django.template.defaultfilters import stringfilter
from opencc import OpenCC


cc = OpenCC("t2s")
register = template.Library()


@register.filter
@stringfilter
def highlight(text, search):
    for s in cc.convert(search.strip().lower()).split(" "):
        if s:
            p = cc.convert(text.lower()).find(s)
            if p != -1:
                text = f'{text[0:p]}<span class="highlight">{text[p:p+len(s)]}</span>{text[p+len(s):]}'
    return mark_safe(text)
