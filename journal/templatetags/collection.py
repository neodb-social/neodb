from django import template
from journal.models import Collection, Like
from django.shortcuts import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def user_visibility_of(context, piece):
    user = context["request"].user
    return piece.is_visible_to(user)


@register.simple_tag()
def user_progress_of(collection, user):
    return (
        collection.get_progress_for_user(user) if user and user.is_authenticated else 0
    )
