"""Collapse runs of consecutive same-author mark posts in the in-app home feed.

When a user bulk-marks many catalog items (e.g. importing from Douban/Goodreads),
each mark becomes its own ``takahe.models.TimelineEvent`` and would otherwise flood
followers' home feed with one card per mark. ``group_feed_events`` folds a run of
consecutive same-author mark events into a single :class:`FeedEventGroup` that the
feed template renders as one "marked N items" card with a cover carousel.

This is a presentation-only concern: it operates on the already-fetched events of
``social.views.data`` and changes nothing about the underlying posts or their
ActivityPub representation.
"""

from collections.abc import Sequence
from typing import Any, Protocol

# Minimum length of a consecutive same-author mark run to collapse into a card.
# Runs shorter than this render as individual cards, unchanged from before.
GROUP_THRESHOLD = 3

# When True, a mark carrying a written comment renders on its own (and breaks a
# run) so authored text is never hidden inside a collapsed group. Flip to False
# to group commented marks too.
GROUP_STANDALONE_COMMENTED = True


class FeedEvent(Protocol):
    """The subset of ``takahe.models.TimelineEvent`` that grouping reads.

    Declared as a Protocol so grouping is honest about duck-typing: real timeline
    events and lightweight test doubles both satisfy it structurally. (Django's
    implicit ``id``/foreign-key ``_id`` attributes are not visible to the type
    checker on the concrete model, so a nominal annotation would be inaccurate.)
    """

    id: int
    type: str
    published: Any
    subject_identity: Any
    subject_identity_id: int
    subject_post: Any


class FeedEventGroup:
    """Presentation-only collapse of consecutive same-author mark events.

    Not persisted. Duck-types the parts of ``takahe.models.TimelineEvent`` that
    ``feed_events.html`` touches, so the template can branch on ``is_group``.
    """

    is_group = True
    type = "post"

    def __init__(self, events: Sequence[FeedEvent]) -> None:
        # events are newest-first, matching the feed query (order_by -id).
        self.events = events
        first = events[0]
        self.identity = first.subject_identity
        self.author = first.subject_post.author
        self.published = first.published
        self.posts = [e.subject_post for e in events]
        # Distinct marked items, newest-first (re-shelving can link several posts
        # to the same item over time; dedupe so a cover is not shown twice).
        seen: set[int] = set()
        items: list = []
        for post in self.posts:
            item = getattr(post, "item", None)
            if item is not None and item.pk not in seen:
                seen.add(item.pk)
                items.append(item)
        self.items = items
        self.count = len(items)

    @property
    def pk(self) -> int:
        # Oldest underlying event id. The HTMX "load more" sentinel uses the last
        # rendered row's pk as ?last=<id> and the view filters id__lt=<id>, so the
        # cursor must be the smallest id in the group to continue without skips.
        return min(e.id for e in self.events)


def _is_mark_event(event: FeedEvent) -> bool:
    """True for a (non-boost) post whose piece is a shelf mark."""
    if getattr(event, "type", None) != "post":
        return False
    post = getattr(event, "subject_post", None)
    if post is None:
        return False
    piece = getattr(post, "piece", None)  # attached by prefetch_pieces_for_posts
    return piece is not None and getattr(piece, "classname", None) == "shelfmember"


def _has_comment(event: FeedEvent) -> bool:
    """True if the mark post carries a written comment.

    Detected from the already-loaded ``type_data`` (no extra queries): a comment
    appears as a ``{"type": "Comment", ...}`` entry in
    ``type_data["object"]["relatedWith"]`` (see ``ShelfMember.get_ap_data``).
    """
    post = getattr(event, "subject_post", None)
    type_data = getattr(post, "type_data", None)
    obj = type_data.get("object") if isinstance(type_data, dict) else None
    related = obj.get("relatedWith") if isinstance(obj, dict) else None
    if not isinstance(related, list):
        return False
    return any(isinstance(r, dict) and r.get("type") == "Comment" for r in related)


def _groupable(event: FeedEvent) -> bool:
    if not _is_mark_event(event):
        return False
    if GROUP_STANDALONE_COMMENTED and _has_comment(event):
        return False
    return True


def group_feed_events(
    events: Sequence[FeedEvent],
) -> list[FeedEvent | FeedEventGroup]:
    """Collapse maximal runs of consecutive same-author groupable mark events.

    A run of length >= ``GROUP_THRESHOLD`` becomes one :class:`FeedEventGroup`;
    everything else (boosts, notes, reviews, articles, replies, commented marks,
    and sub-threshold runs) passes through unchanged and in order. A run is broken
    by a different author, a non-mark post, a boost, or a commented mark.

    Grouping is intentionally per page: the view fetches a fixed ``PAGE_SIZE``
    slice and we group only within it. A burst larger than a page naturally splits
    across pages ("cap and split"), which keeps the query cost fixed and the HTMX
    cursor trivially correct (``FeedEventGroup.pk`` returns the oldest id).
    """
    result: list[FeedEvent | FeedEventGroup] = []
    i = 0
    n = len(events)
    while i < n:
        event = events[i]
        if not _groupable(event):
            result.append(event)
            i += 1
            continue
        author_id = event.subject_identity_id
        j = i + 1
        while (
            j < n
            and _groupable(events[j])
            and events[j].subject_identity_id == author_id
        ):
            j += 1
        run = events[i:j]
        if len(run) >= GROUP_THRESHOLD:
            result.append(FeedEventGroup(run))
        else:
            result.extend(run)
        i = j
    return result
