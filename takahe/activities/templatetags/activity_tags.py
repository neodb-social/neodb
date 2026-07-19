import datetime
from urllib.parse import urlencode

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def timedeltashort(value: datetime.datetime):
    """
    A more compact version of timesince
    """
    if not value:
        return ""
    delta = timezone.now() - value
    seconds = int(delta.total_seconds())
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    days = abs(delta.days)
    if seconds < 60:
        text = f"{seconds:0n}s"
    elif seconds < 60 * 60:
        minutes = seconds // 60
        text = f"{minutes:0n}m"
    elif seconds < 60 * 60 * 24:
        hours = seconds // (60 * 60)
        text = f"{hours:0n}h"
    elif days < 365:
        text = f"{days:0n}d"
    else:
        years = max(days // 365.25, 1)
        text = f"{years:0n}y"
    return sign + text


@register.filter
def timedeltashortenddate(value: datetime.datetime):
    """
    Formatter for end dates - timedeltashort but it adds "ended ... ago" or
    "left" depending on the direction.
    """
    output = timedeltashort(value)
    if output.startswith("-"):
        return f"{output[1:]} left"
    else:
        return f"Ended {output} ago"


@register.simple_tag(takes_context=True)
def urlparams(context, **kwargs):
    """
    Generates a URL parameter string the same as the current page but with
    the given items changed.
    """
    params = dict(context["request"].GET.items())
    for name, value in kwargs.items():
        if value:
            params[name] = value
        elif name in params:
            del params[name]
    return urlencode(params)


@register.simple_tag(takes_context=True)
def poll_vote_context(context, post):
    """Return browser-session voting state for a Question post."""
    request = context.get("request")
    identity = None
    identities = []

    if request is not None and request.user.is_authenticated:
        identities = list(request.user.identities.all())
        requested_identity_id = request.GET.get("identity")
        session = getattr(request, "session", None)
        session_identity_id = session.get("identity_id") if session else None
        selected_identity_id = requested_identity_id or session_identity_id
        identity = next(
            (
                candidate
                for candidate in identities
                if str(candidate.pk) == str(selected_identity_id)
            ),
            identities[0] if identities else None,
        )

    poll = post.type_data.to_mastodon_json(post, identity=identity)
    own_votes = set(poll["own_votes"])
    option_count = len(poll["options"])
    can_vote = bool(
        identity
        and identity.pk != post.author_id
        and not poll["expired"]
        and (
            (poll["multiple"] and len(own_votes) < option_count)
            or (not poll["multiple"] and not poll["voted"])
        )
    )
    return {
        "authenticated": bool(request and request.user.is_authenticated),
        "can_vote": can_vote,
        "identities": identities,
        "identity": identity,
        "multiple_identities": len(identities) > 1,
        "own_poll": bool(identity and identity.pk == post.author_id),
        "own_votes": own_votes,
        "poll": poll,
    }
