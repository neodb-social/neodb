from django import template
from django.conf import settings
from django.template.defaultfilters import stringfilter
from django.utils.translation import gettext_lazy as _

from users.models import APIdentity, User

register = template.Library()


@register.simple_tag
def mastodon(domain):
    url = "https://" + domain
    return url


@register.simple_tag(takes_context=True)
def current_user_relationship(context, user: "User"):
    current_user = context["request"].user
    r = {
        "requesting": False,
        "following": False,
        "unfollowable": False,
        "muting": False,
        "unmutable": False,
        "rejecting": False,
        "status": "",
    }
    if current_user and current_user.is_authenticated and current_user != user:
        current_identity = context["request"].user.identity
        target_identity = user.identity
        if current_identity.is_blocking(
            target_identity
        ) or current_identity.is_blocked_by(target_identity):
            r["rejecting"] = True
        else:
            r["muting"] = current_identity.is_muting(target_identity)
            r["unmutable"] = r["muting"]
            r["following"] = current_identity.is_following(target_identity)
            r["unfollowable"] = r["following"]
            if r["following"]:
                if current_identity.is_followed_by(target_identity):
                    r["status"] = _("互相关注")
                else:
                    r["status"] = _("已关注")
            else:
                if current_identity.is_followed_by(target_identity):
                    r["status"] = _("被ta关注")
    return r
