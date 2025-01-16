from django import template
from django.utils.translation import gettext as _

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
        "unavailable": False,
        "requesting": False,
        "requested": False,
        "following": False,
        "muting": False,
        "rejecting": False,
        "status": "",
    }
    if target_identity and current_identity and not target_identity.restricted:
        if current_identity != target_identity:
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
                        r["status"] = _("mutual followed")
                    else:
                        r["status"] = _("followed")
                else:
                    if current_identity.is_followed_by(target_identity):
                        r["status"] = _("following you")
        else:
            r["unavailable"] = True
            r["status"] = _("you")
    else:
        r["unavailable"] = True
        r["status"] = _("unavailable")
    return r
