from django import template
from django.conf import settings
from django.utils.html import format_html


register = template.Library()


@register.simple_tag
def admin_url():
    url = settings.ADMIN_URL
    if not url.startswith("/"):
        url = "/" + url
    if not url.endswith("/"):
        url += "/"
    return format_html(url)
