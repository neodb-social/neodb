from django import template
from django.utils.safestring import mark_safe
from django.template.defaultfilters import stringfilter
from opencc import OpenCC


cc = OpenCC("t2s")
register = template.Library()


@register.filter
@stringfilter
def highlight(text, search):
    otext = cc.convert(text.lower())
    rtext = ""
    words = list(set([w for w in cc.convert(search.strip().lower()).split(" ") if w]))
    words.sort(key=len, reverse=True)
    i = 0
    while i < len(otext):
        m = None
        for w in words:
            if otext[i : i + len(w)] == w:
                m = f'<span class="highlight">{text[i:i+len(w)]}</span>'
                i += len(w)
                break
        if not m:
            m = text[i]
            i += 1
        rtext += m
    return mark_safe(rtext)
