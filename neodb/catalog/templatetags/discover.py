from typing import Iterable

from django import template

from catalog.models import Item, ItemCategory, ItemCredit

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


def primary_credit_entry(item: Item) -> ItemCredit | None:
    credits = item.role_credits
    for role in PRIMARY_CREDIT_ROLES.get(item.category, ()):
        entries = credits.get(role)
        if entries:
            return entries[0]
    for entries in credits.values():
        if entries:
            return entries[0]
    return None


def attach_primary_credit_names(items: Iterable[Item]) -> None:
    """Localize only the credit each card shows.

    A film can carry dozens of cast credits, and each localized name is read
    out of the person's large metadata JSON.
    """
    ItemCredit.attach_localized_names(
        credit for item in items if (credit := primary_credit_entry(item))
    )


@register.filter
def primary_credit(item: Item) -> str:
    """Name of the credit a card leads with: author, director, artist and so on.

    Reads the prefetched ``role_credits`` only, so callers must batch-load
    credits (the discover job and view do) or this costs a query per card.
    """
    credit = primary_credit_entry(item)
    return credit.display_name if credit else ""


@register.filter
def cover_shape(item: Item) -> str:
    """CSS class for the cover box: albums and podcasts are square, the rest 2:3."""
    return "sq" if item.category in SQUARE_COVER_CATEGORIES else "tall"
