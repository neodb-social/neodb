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
        "unfollowable": False,
        "muting": False,
        "unmutable": False,
        "rejecting": False,
        "status": "",
    }
    if current_user and current_user.is_authenticated and current_user != user:
        if current_user.is_blocking(user) or user.is_blocking(current_user):
            r["rejecting"] = True
        else:
            r["muting"] = current_user.is_muting(user)
            if user in current_user.local_muting.all():
                r["unmutable"] = current_user
            if current_user.is_following(user):
                r["following"] = True
                if user in current_user.local_following.all():
                    r["unfollowable"] = True
                if current_user.is_followed_by(user):
                    r["status"] = _("互相关注")
                else:
                    r["status"] = _("已关注")
            else:
                if current_user.is_followed_by(user):
                    r["status"] = _("被ta关注")
    return r
