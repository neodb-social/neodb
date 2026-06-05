"""Builders for NeoDB-owned ATProto records published to a user's PDS.

These records live under the project-controlled ``net.neodb.*`` lexicon
namespace so other ATProto applications can read a user's NeoDB marks and
reviews (with ratings embedded) directly from their repository. The lexicon
definitions are documented under ``docs/lexicons/net/neodb/``.

NeoDB catalog items are not themselves ATProto records, so a work is
referenced inline via :func:`build_subject` (NeoDB permalink, source URLs
and standardized identifiers) rather than a ``com.atproto.repo.strongRef``.
"""

from datetime import datetime
from datetime import timezone as dt_timezone
from typing import TYPE_CHECKING, Any

from catalog.models import IdealIdTypes

if TYPE_CHECKING:
    from catalog.models import Item

NAMESPACE = "net.neodb"
REVIEW_NSID = f"{NAMESPACE}.review"
MARK_NSID = f"{NAMESPACE}.mark"

RATING_MAX = 10

# (collection NSID, record body) for records that should currently exist.
# The record key is derived centrally from Piece.atproto_rkey() (the piece's
# own uuid) rather than carried here.
AtprotoRecord = tuple[str, dict[str, Any]]


def format_datetime(dt: datetime) -> str:
    """Render a datetime as an RFC3339 / ATProto timestamp (UTC, ``Z``)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return (
        dt.astimezone(dt_timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def build_subject(item: "Item") -> dict[str, Any]:
    """Inline reference to a catalog item used as a record's subject.

    The work and its external sources are referenced by URL -- the NeoDB
    permalink (``uri``) and each source site's resource URL (``sources``) --
    never by site-specific raw ids; standardized identifiers (IdealIdTypes,
    e.g. ISBN / IMDB / WikiData) are additionally listed in ``identifiers``.
    Field names mirror NeoDB's API schema: ``category`` is the broad media
    category while ``type`` is the specific NeoDB class, so TV show / season
    / episode (and podcast / episode, performance / production) remain
    distinguishable.
    """
    subject: dict[str, Any] = {
        "uri": item.absolute_url,
        "category": str(item.category) if item.category else "",
        "type": item.ap_object_type,
        "title": item.display_title,
    }
    if item.has_cover():
        subject["cover"] = item.cover_image_url
    sources: list[str] = []
    identifiers: dict[str, str] = {}
    for res in item.external_resources.order_by("id_type", "id_value"):
        if res.url:
            sources.append(res.url)
        lookup_ids = dict(res.other_lookup_ids or {})
        lookup_ids[res.id_type] = res.id_value
        for t, v in lookup_ids.items():
            if v and t in IdealIdTypes:
                identifiers[t] = v
    if item.primary_lookup_id_type in IdealIdTypes and item.primary_lookup_id_value:
        identifiers[item.primary_lookup_id_type] = item.primary_lookup_id_value
    if sources:
        subject["sources"] = sources
    if identifiers:
        subject["identifiers"] = [
            {"type": t, "value": identifiers[t]} for t in sorted(identifiers)
        ]
    return subject


def build_rating(grade: int | None) -> dict[str, Any] | None:
    """Render a 1-10 grade as a rating object, or ``None`` when unset."""
    if not grade:
        return None
    return {"value": grade, "max": RATING_MAX}
