from django import template
from django.template.defaultfilters import stringfilter
from django.utils.translation import ngettext

from journal.models import Collection
from journal.models.mixins import UserOwnedObjectMixin
from users.models.apidentity import APIdentity
from users.models.user import User

register = template.Library()


@register.simple_tag(takes_context=True)
def user_visibility_of(context, piece: UserOwnedObjectMixin):
    user = context["request"].user
    return piece.is_visible_to(user)  # type: ignore


@register.simple_tag()
def user_progress_of(collection: Collection, user: User):
    return (
        collection.get_progress(user.identity) if user and user.is_authenticated else 0
    )


@register.simple_tag()
def user_stats_of(collection: Collection, identity: APIdentity):
    return collection.get_stats(identity) if identity else {}


@register.simple_tag()
def prural_items(count: int, category: str):
    match category:
        case "book":
            return ngettext(
                "%(count)d book",
                "%(count)d books",
                count,
            ) % {
                "count": count,
            }
        case "movie":
            return ngettext(
                "%(count)d movie",
                "%(count)d movies",
                count,
            ) % {
                "count": count,
            }
        case "tv":
            return ngettext(
                "%(count)d tv show",
                "%(count)d tv shows",
                count,
            ) % {
                "count": count,
            }
        case "music":
            return ngettext(
                "%(count)d album",
                "%(count)d albums",
                count,
            ) % {
                "count": count,
            }
        case "game":
            return ngettext(
                "%(count)d game",
                "%(count)d games",
                count,
            ) % {
                "count": count,
            }
        case "podcast":
            return ngettext(
                "%(count)d podcast",
                "%(count)d podcasts",
                count,
            ) % {
                "count": count,
            }
        case "performance":
            return ngettext(
                "%(count)d performance",
                "%(count)d performances",
                count,
            ) % {
                "count": count,
            }
        case _:
            return ngettext(
                "%(count)d item",
                "%(count)d items",
                count,
            ) % {
                "count": count,
            }
