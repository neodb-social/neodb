from django import template

from catalog.models import Item, ItemCategory

register = template.Library()

# The one credit a discover card leads with, per category, in order of
# preference. Anything else falls back to the first credit on the item.
PRIMARY_CREDIT_ROLES: dict[ItemCategory, tuple[str, ...]] = {
    ItemCategory.Book: ("author", "translator"),
    ItemCategory.Movie: ("director",),
    ItemCategory.TV: ("director", "playwright"),
    ItemCategory.Game: ("developer", "publisher"),
    ItemCategory.Music: ("artist",),
    ItemCategory.Podcast: ("host",),
    ItemCategory.Performance: ("playwright", "director", "composer"),
}

SQUARE_COVER_CATEGORIES = {ItemCategory.Music, ItemCategory.Podcast}


@register.filter
def primary_credit(item: Item) -> str:
    """Name of the credit a card leads with: author, director, artist and so on.

    Reads the prefetched ``role_credits`` only, so callers must batch-load
    credits (the discover job and view do) or this costs a query per card.
    """
    credits = item.role_credits
    for role in PRIMARY_CREDIT_ROLES.get(item.category, ()):
        entries = credits.get(role)
        if entries:
            return entries[0].display_name
    for entries in credits.values():
        if entries:
            return entries[0].display_name
    return ""


@register.filter
def cover_shape(item: Item) -> str:
    """CSS class for the cover box: albums and podcasts are square, the rest 2:3."""
    return "sq" if item.category in SQUARE_COVER_CATEGORIES else "tall"
