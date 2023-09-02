from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def prettydate(d):
    # TODO use date and naturaltime instead https://docs.djangoproject.com/en/3.2/ref/contrib/humanize/
    diff = timezone.now() - d
    s = diff.seconds
    if diff.days > 14 or diff.days < 0:
        return d.strftime("%Y年%m月%d日")
    elif diff.days >= 1:
        return "{} 天前".format(diff.days)
    elif s < 120:
        return "刚刚"
    elif s < 3600:
        return "{} 分钟前".format(s // 60)
    else:
        return "{} 小时前".format(s // 3600)
