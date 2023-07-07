from django import template
from django.conf import settings
from django.template.defaultfilters import stringfilter
from django.utils.translation import gettext_lazy as _


register = template.Library()


@register.simple_tag
def mastodon(domain):
    url = "https://" + domain
    return url


@register.simple_tag(takes_context=True)
def current_user_relationship(context, user):
    current_user = context["request"].user
    r = {
        "following": False,
        "followable": False,
        "unfollowable": False,
        "status": "",
    }
    if (
        current_user
        and current_user.is_authenticated
        and current_user != user
        and not current_user.is_blocked_by(user)
    ):
        if current_user.is_following(user):
            r["following"] = True
            if user in current_user.local_following.all():
                r["unfollowable"] = True
            if current_user.is_followed_by(user):
                r["status"] = _("互相关注")
            else:
                r["status"] = _("已关注")
        else:
            r["followable"] = True
            if current_user.is_followed_by(user):
                r["status"] = _("被ta关注")
    return r
