from django import template
from django.utils import timezone
from django.utils.translation import gettext as _

register = template.Library()


@register.filter
def prettydate(d):
    # TODO use date and naturaltime instead https://docs.djangoproject.com/en/3.2/ref/contrib/humanize/
    diff = timezone.now() - d
    s = diff.seconds
    if diff.days > 14 or diff.days < 0:
        return d.strftime("%Y-%m-%d")
    elif diff.days >= 1:
        return "{}d".format(diff.days)
    elif s < 120:
        return _("just now")
    elif s < 3600:
        return "{}m".format(s // 60)
    else:
        return "{}h".format(s // 3600)
