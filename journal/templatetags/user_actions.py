from django import template
from django.urls import reverse

from takahe.utils import Takahe

register = template.Library()


@register.simple_tag(takes_context=True)
def wish_item_action(context, item):
    user = context["request"].user
    action = {}
    if user and user.is_authenticated and item:
        action = {
            "taken": user.shelf_manager.locate_item(item) is not None,
            "url": reverse("journal:wish", args=[item.uuid]),
        }
    return action


@register.simple_tag(takes_context=True)
def liked_piece(context, piece):
    user = context["request"].user
    return user and user.is_authenticated and piece.is_liked_by(user.identity)


@register.simple_tag(takes_context=True)
def liked_post(context, post):
    user = context["request"].user
    return (
        user
        and user.is_authenticated
        and Takahe.post_liked_by(post.pk, user.identity.pk)
    )


@register.simple_tag(takes_context=True)
def boosted_post(context, post):
    user = context["request"].user
    return (
        user
        and user.is_authenticated
        and Takahe.post_boosted_by(post.pk, user.identity.pk)
    )
