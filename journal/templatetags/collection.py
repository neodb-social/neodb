from django import template
from django.template.defaultfilters import stringfilter

from journal.models import Collection
from journal.models.mixins import UserOwnedObjectMixin
from users.models.apidentity import APIdentity
from users.models.user import User

register = template.Library()


@register.simple_tag(takes_context=True)
def user_visibility_of(context, piece: UserOwnedObjectMixin):
    user = context["request"].user
    return piece.is_visible_to(user)


@register.simple_tag()
def user_progress_of(collection: Collection, user: User):
    return (
        collection.get_progress(user.identity) if user and user.is_authenticated else 0
    )


@register.simple_tag()
def user_stats_of(collection: Collection, identity: APIdentity):
    return collection.get_stats(identity) if identity else {}


@register.filter(is_safe=True)
@stringfilter
def prural_items(category: str):
    # TODO support i18n here
    # return _(f"items of {category}")
    if category == "book":
        return "本书"
    elif category == "movie":
        return "部电影"
    elif category == "tv":
        return "部剧集"
    elif category == "album" or category == "music":
        return "张专辑"
    elif category == "game":
        return "个游戏"
    elif category == "podcast":
        return "个播客"
    elif category == "performance":
        return "场演出"
    else:
        return category
