from django import template
from django.conf import settings
from django.template.defaultfilters import stringfilter


register = template.Library()


@register.simple_tag
def mastodon(domain):
    url = "https://" + domain
    return url


@register.simple_tag(takes_context=True)
def current_user_relationship(context, user):
    current_user = context["request"].user
    if current_user and current_user.is_authenticated:
        if current_user.is_following(user):
            if current_user.is_followed_by(user):
                return "互相关注"
            else:
                return "已关注"
        elif current_user.is_followed_by(user):
            return "被ta关注"
    return None
