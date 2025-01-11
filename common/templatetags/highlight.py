from django import template
from django.template.defaultfilters import stringfilter
from django.utils.safestring import mark_safe

register = template.Library()


# opencc is removed for now due to package installation issues
# to re-enable it, add it to Dockerfile/requirements.txt and uncomment the following lines
# from opencc import OpenCC
# cc = OpenCC("t2s")
def _cc(text):
    return text
    # return cc.convert(text)


@register.filter
@stringfilter
def highlight(text, search):
    otext = _cc(text.lower())
    sl = len(text)
    if sl != len(otext):
        return text  # in rare cases, the lowered&converted text has a different length
    rtext = ""
    words = list(set([w for w in _cc(search.strip().lower()).split(" ") if w]))
    words.sort(key=len, reverse=True)
    i = 0
    while i < sl:
        m = None
        for w in words:
            if otext[i : i + len(w)] == w:
                m = f"<mark>{text[i : i + len(w)]}</mark>"
                i += len(w)
                break
        if not m:
            m = text[i]
            i += 1
        rtext += m
    return mark_safe(rtext)
