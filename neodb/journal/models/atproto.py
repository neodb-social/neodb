"""Builders for NeoDB-owned ATProto records published to a user's PDS.

These records live under the project-controlled ``net.neodb.*`` lexicon
namespace so other ATProto applications can read a user's NeoDB marks and
reviews (with ratings embedded) directly from their repository. The lexicon
definitions are documented under ``docs/lexicons/net/neodb/``.

Long-form pieces (reviews and articles) are additionally published as
``site.standard.document`` records (https://standard.site/) so generic
ATProto publishing apps can read them; see :func:`build_document`.

NeoDB catalog items are not themselves ATProto records, so a work is
referenced inline via :func:`build_subject` (NeoDB permalink, source URLs
and standardized identifiers) rather than a ``com.atproto.repo.strongRef``.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import TYPE_CHECKING, Any

from django.conf import settings

from catalog.models import IdealIdTypes

if TYPE_CHECKING:
    from catalog.models import Item

    from .common import Piece

NAMESPACE = "net.neodb"
REVIEW_NSID = f"{NAMESPACE}.review"
MARK_NSID = f"{NAMESPACE}.mark"

# standard.site lexicon (https://standard.site/docs/lexicons/document) for
# long-form documents, plus the at.markpub.markdown convention carried in
# the document's open ``content`` union (https://markpub.at/)
DOCUMENT_NSID = "site.standard.document"
MARKPUB_MARKDOWN_NSID = "at.markpub.markdown"
MARKPUB_TEXT_NSID = "at.markpub.text"

RATING_MAX = 10

DOCUMENT_DESCRIPTION_MAX_GRAPHEMES = 3000
DOCUMENT_TAG_MAX_GRAPHEMES = 128

_TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"  # base32-sortable
_TID_CLOCKID_BITS = 10
_TID_TIMESTAMP_BITS = 53
_TID_LENGTH = 13
_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

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


def build_fediverse_uri(piece: "Piece") -> str | None:
    """ActivityPub object URI of the post this record mirrors, or ``None``.

    Lets consumers of a ``net.neodb.*`` record follow it back to the original
    fediverse post. The value is the linked timeline Note's ``object_uri``;
    it is absent when the piece has no linked post (e.g. never crossposted).
    """
    post = piece.latest_post
    return post.object_uri if post else None


def build_document_rkey(piece: "Piece") -> str:
    """Deterministic TID record key for a piece's ``site.standard.document``.

    The lexicon requires ``tid`` record keys, so the piece uuid (used for
    ``net.neodb.*`` records) is not acceptable. A TID is derived from the
    piece itself -- creation time as the timestamp bits, primary key as the
    clock-id bits -- so it needs no stored state and stays idempotent across
    sync retries. ``created_time`` is user-editable, so the first successful
    sync freezes the key in ``metadata["atproto_document_rkey"]`` (see
    ``Piece.atproto_document_rkey``) to keep later backdating from orphaning
    the PDS record.
    """
    dt = piece.created_time
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    micros = (dt.astimezone(dt_timezone.utc) - _EPOCH) // timedelta(microseconds=1)
    micros &= (1 << _TID_TIMESTAMP_BITS) - 1
    clockid = (piece.pk or 0) % (1 << _TID_CLOCKID_BITS)
    n = (micros << _TID_CLOCKID_BITS) | clockid
    chars = []
    for _ in range(_TID_LENGTH):
        chars.append(_TID_ALPHABET[n & 0x1F])
        n >>= 5
    return "".join(reversed(chars))


def build_document(
    piece: "Piece",
    *,
    title: str,
    body: str,
    text: str,
    description: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """``site.standard.document`` record for a long-form piece.

    Published as a "loose" document: ``site`` is the instance base URL and
    ``path`` the piece's local path, so ``site + path`` reconstructs the
    canonical NeoDB URL. The full markdown ``body`` travels in the open
    ``content`` union (``at.markpub.markdown``) next to the plaintext
    ``textContent``, and the crossposted skeet (when one exists) is linked
    via ``bskyPostRef`` so off-platform comments stay discoverable.
    """
    record: dict[str, Any] = {
        "$type": DOCUMENT_NSID,
        "site": settings.SITE_INFO["site_url"].rstrip("/"),
        "path": piece.url,
        "title": title,
        "publishedAt": format_datetime(piece.created_time),
        "textContent": text,
        "content": {
            "$type": MARKPUB_MARKDOWN_NSID,
            "text": {"$type": MARKPUB_TEXT_NSID, "markdown": body},
        },
    }
    # created_time is set at instantiation but edited_time at a later DB
    # write, so they always differ by a sliver; only a real gap is an edit
    if (
        piece.edited_time
        and (piece.edited_time - piece.created_time).total_seconds() > 1
    ):
        record["updatedAt"] = format_datetime(piece.edited_time)
    if description:
        record["description"] = description[:DOCUMENT_DESCRIPTION_MAX_GRAPHEMES]
    if tags:
        record["tags"] = [t for t in tags if len(t) <= DOCUMENT_TAG_MAX_GRAPHEMES]
    metadata = getattr(piece, "metadata", None) or {}
    post_uri = metadata.get("bluesky_id")
    post_cid = metadata.get("bluesky_cid")
    if post_uri and post_cid:
        record["bskyPostRef"] = {"uri": post_uri, "cid": post_cid}
    return record
