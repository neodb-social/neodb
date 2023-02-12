from django import template
from journal.models import Collection, Like
from django.shortcuts import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def wish_item_action(context, item):
    user = context["request"].user
    action = {}
    if user and user.is_authenticated:
        action = {
            "taken": user.shelf_manager.locate_item(item) is not None,
            "url": reverse("journal:wish", args=[item.uuid]),
        }
    return action


@register.simple_tag(takes_context=True)
def like_piece_action(context, piece):
    user = context["request"].user
    action = {}
    if user and user.is_authenticated:
        action = {
            "taken": piece.owner == user
            or Like.objects.filter(target=piece, owner=user).first() is not None,
            "url": reverse("journal:like", args=[piece.uuid]),
        }
    return action


@register.simple_tag(takes_context=True)
def liked_piece(context, piece):
    user = context["request"].user
    return user and user.is_authenticated and Like.user_liked_piece(user, piece)
