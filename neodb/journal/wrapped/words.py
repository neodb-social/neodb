"""The "One title, your words" card: the member's texts of the year, for the picker and for the card."""

import logging
import uuid
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo


from catalog.models import (
    Item,
)
from users.models import APIdentity

from journal.models import (
    Comment,
    Note,
    Review,
)

from .base import (
    CATEGORIES,
    _card_item,
    _credits,
    _grades,
    _is_uuid,
    _items_by_id,
    plain_excerpt,
    year_range,
)

logger = logging.getLogger(__name__)


# the "your words" card: one text of the member's on one title, up to this many characters, then cut
WORDS_CHARS = 600


WORDS_KINDS = ("comment", "note", "review")


def _words_model(kind: str):
    return {"comment": Comment, "note": Note, "review": Review}[kind]


def words_text(kind: str, piece: Any) -> dict[str, Any]:
    """The plain text of a comment, note or review for the card, spoilers taken out, and whether it was cut."""
    if kind == "comment":
        raw, title, progress = piece.text, None, ""
    elif kind == "note":
        raw, title, progress = piece.content, piece.title, piece.progress_display
    else:
        raw, title, progress = piece.body, piece.title, ""
    plain = plain_excerpt(raw, WORDS_CHARS + 1)
    return {
        "text": plain[:WORDS_CHARS],
        "more": len(plain) > WORDS_CHARS,
        "title": title or None,
        "progress": progress or "",
        "chars": len(raw or ""),
    }


def _words_item(
    it: Item, rating: int | None, credits: dict[int, list[list[str]]]
) -> dict[str, Any]:
    return {**_card_item(it), "rating": rating, "credits": credits.get(it.pk, [])}


def resolve_words(
    owner: APIdentity, made: list[dict[str, Any]], level: int, tz: ZoneInfo
) -> dict[str, dict[str, Any]]:
    """For each made "words" card, by card id, the text it shows at this level.

    A card whose text is gone, or is more private than the level, is not in the result, so its text
    never leaves the server for that audience.
    """
    # one text can back several cards, each with its own caption
    refs: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for m in made:
        src = m.get("source") or {}
        if (
            src.get("type") == "words"
            and src.get("kind") in WORDS_KINDS
            and _is_uuid(src.get("ref"))
        ):
            refs[src["kind"]][str(uuid.UUID(src["ref"]))].append(str(m.get("id")))
    found: list[tuple[str, str, Any]] = []
    for kind, cards in refs.items():
        for piece in _words_model(kind).objects.filter(
            owner=owner, uid__in=list(cards), visibility__lte=level
        ):
            found += [(card_id, kind, piece) for card_id in cards[str(piece.uid)]]
    items = _items_by_id(list({p.item_id for _, _, p in found}))
    credits = _credits(list(items))
    grades = _grades(owner, list(items), level)
    out = {}
    for card_id, kind, piece in found:
        it = items.get(piece.item_id)
        if not it or it.is_deleted:
            continue
        out[card_id] = {
            "kind": kind,
            **words_text(kind, piece),
            "date": piece.created_time.astimezone(tz).date().isoformat(),
            "item": _words_item(it, grades.get(it.pk), credits),
        }
    return out


def words_menu(
    owner: APIdentity, year: int, tz: ZoneInfo, limit: int = 400
) -> list[dict[str, Any]]:
    """Every title the owner wrote about in the year, with the comments, notes and reviews, newest first."""
    start, end = year_range(year, tz)
    rows: list[tuple[str, Any]] = []
    for kind in WORDS_KINDS:
        rows += [
            (kind, p)
            for p in _words_model(kind)
            .objects.filter(owner=owner, created_time__gte=start, created_time__lt=end)
            .order_by("-created_time")[:limit]
        ]
    rows = sorted(rows, key=lambda r: r[1].created_time, reverse=True)[:limit]
    items = _items_by_id(list({p.item_id for _, p in rows}))
    grades = _grades(owner, list(items))
    credits = _credits(list(items))
    works: dict[int, dict[str, Any]] = {}
    for kind, p in rows:
        it = items.get(p.item_id)
        if not it or it.is_deleted or str(it.category) not in CATEGORIES:
            continue
        w = works.get(it.pk)
        if w is None:
            w = works[it.pk] = {
                **_words_item(it, grades.get(it.pk), credits),
                "texts": [],
            }
        w["texts"].append(
            {
                "kind": kind,
                "ref": str(p.uid),
                "vis": int(p.visibility),
                "date": p.created_time.astimezone(tz).date().isoformat(),
                "sensitive": bool(getattr(p, "sensitive", False)),
                **words_text(kind, p),
            }
        )
    return list(works.values())
