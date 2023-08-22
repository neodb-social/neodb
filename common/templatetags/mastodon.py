from django import template
from django.utils.translation import gettext_lazy as _

from users.models import APIdentity

register = template.Library()


@register.simple_tag
def mastodon(domain):
    url = "https://" + domain
    return url


@register.simple_tag(takes_context=True)
def current_user_relationship(context, target_identity: "APIdentity"):
    current_identity: "APIdentity | None" = (
        context["request"].user.identity
        if context["request"].user.is_authenticated
        else None
    )
    r = {
        "requesting": False,
        "requested": False,
        "following": False,
        "muting": False,
        "rejecting": False,
        "status": "",
    }
    if current_identity and current_identity != target_identity:
        if current_identity.is_blocking(
            target_identity
        ) or current_identity.is_blocked_by(target_identity):
            r["rejecting"] = True
        else:
            r["requesting"] = current_identity.is_requesting(target_identity)
            r["requested"] = current_identity.is_requested(target_identity)
            r["muting"] = current_identity.is_muting(target_identity)
            r["following"] = current_identity.is_following(target_identity)
            if r["following"]:
                if current_identity.is_followed_by(target_identity):
                    r["status"] = _("互相关注")
                else:
                    r["status"] = _("已关注")
            else:
                if current_identity.is_followed_by(target_identity):
                    r["status"] = _("被ta关注")
    return r
