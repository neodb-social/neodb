from urllib.parse import urlparse

import django_rq
from django.db import transaction
from django.utils import timezone
from loguru import logger

from common.validators import is_valid_url
from journal.models.renderers import html_to_text
from users.models import User

from ..models import (
    VerifiedCreator,
    creator_identity_candidates,
    match_creator_identity,
    resolve_creator_identity,
)


def enqueue_creator_verification(claim: VerifiedCreator, user: User) -> None:
    django_rq.get_queue("fetch").enqueue(verify_creator_task, claim.pk, user.pk)


def _fail_claim(claim: VerifiedCreator, reason: str) -> None:
    # conditional update so a stale worker cannot clobber a claim that has
    # been removed, re-verified or manually verified in the meantime
    VerifiedCreator.objects.filter(
        pk=claim.pk, state=VerifiedCreator.State.PENDING
    ).update(
        state=VerifiedCreator.State.FAILED,
        failure_reason=reason,
        edited_time=timezone.now(),
    )


def _has_audio_episode(feed: dict) -> bool:
    """Whether the parsed feed has at least one playable audio episode.

    Guards against verifying a feed that is not actually a podcast (no audio
    enclosures). Enclosures without a declared mime type are accepted, since
    many feeds omit it; clearly non-audio types (e.g. video) do not count.
    """
    for episode in feed.get("episodes") or []:
        for enclosure in episode.get("enclosures") or []:
            mime = (enclosure.get("mime_type") or "").lower()
            if enclosure.get("url") and (mime.startswith("audio") or not mime):
                return True
    return False


# rel="me" links on a page (rel is a space-separated, case-insensitive token list)
_REL_ME_XPATH = (
    "//a[contains(concat(' ', normalize-space(translate(@rel, 'ME', 'me')), ' '),"
    " ' me ')]/@href"
    " | //link[contains(concat(' ', normalize-space(translate(@rel, 'ME', 'me')), ' '),"
    " ' me ')]/@href"
)


def _fetch_page_rel_me_urls(url: str) -> list[str]:
    """rel="me" link targets declared by the page the feed's channel links to.

    A creator's own site typically rel="me"-links to their fediverse and Bluesky
    profiles, so matching these proves authorship across both networks. The page
    URL itself is included too, so a feed whose channel link is the creator's
    Bluesky (its handle is a domain) also counts. Best-effort: [] on any error.
    """
    from catalog.common.downloaders import BasicDownloader2

    if not url or not is_valid_url(url):
        return []
    try:
        content = BasicDownloader2(url).download().html()
    except Exception:
        logger.debug(f"verify_creator_task: unable to fetch page {url}")
        return []
    if content is None:
        return []
    return [h.strip() for h in content.xpath(_REL_ME_XPATH) if h and h.strip()]


def _channel_link_matches_bluesky(user: User, link: str) -> str | None:
    """Proof when the feed's channel link is the user's Bluesky: a Bluesky handle
    is itself a domain, so a channel link hosted on it (any path) counts."""
    bluesky = getattr(user, "bluesky", None)
    handle = getattr(bluesky, "handle", None) if bluesky else None
    if not handle or not link:
        return None
    host = urlparse(link if "://" in link else f"https://{link}").hostname or ""
    if host.lower().removeprefix("www.") == handle.lower().removeprefix("www."):
        return f"@{handle}"
    return None


def _match_creator(user: User, feed: dict) -> str | None:
    """Find proof of ownership in the feed.

    A rel="me" link on the page the channel <link> points to (or the channel link
    being the creator's own Bluesky) is preferred over a text match in the feed
    description; all paths must still match one of the user's own candidates.
    """
    candidates = creator_identity_candidates(user)
    link = feed.get("link") or ""
    if link:
        proofs = [link, *_fetch_page_rel_me_urls(link)]
        matched = match_creator_identity(proofs, candidates)
        if matched:
            return matched
        matched = _channel_link_matches_bluesky(user, link)
        if matched:
            return matched
    description = feed.get("description") or ""
    return match_creator_identity([description, html_to_text(description)], candidates)


def _verify_claim(claim: VerifiedCreator, user: User, matched: str) -> None:
    """Mark the pending claim VERIFIED, attributing it to the identity the
    matched identifier represents (e.g. the user's linked Mastodon identity
    rather than their local one)."""
    identity = resolve_creator_identity(user, matched)
    item = claim.item
    with transaction.atomic():
        pending = (
            VerifiedCreator.objects.select_for_update()
            .filter(pk=claim.pk, state=VerifiedCreator.State.PENDING)
            .first()
        )
        if not pending:
            return
        if identity.pk == pending.owner_id:
            pending.state = VerifiedCreator.State.VERIFIED
            pending.matched = matched
            pending.failure_reason = ""
            pending.edited_time = timezone.now()
            pending.save(
                update_fields=["state", "matched", "failure_reason", "edited_time"]
            )
            verified = pending
        else:
            # attribute the work to the matched identity; the original pending
            # row is dropped so the (item, owner) uniqueness still holds.
            # edited_time is set explicitly: update_or_create saves an existing
            # row with update_fields limited to defaults, so the auto_now field
            # would not refresh on the update branch otherwise.
            pending.delete()
            verified, _ = VerifiedCreator.objects.update_or_create(
                item=item,
                owner=identity,
                defaults={
                    "state": VerifiedCreator.State.VERIFIED,
                    "matched": matched,
                    "failure_reason": "",
                    "edited_time": timezone.now(),
                },
            )
        # log inside the transaction so the state change and its audit entry
        # commit together
        item.log_action({"!creator_verified": ["", f"{verified.owner} ({matched})"]})
    logger.info(f"creator verification matched {matched} for {verified}")


def verify_creator_task(claim_id: int, user_id: int) -> None:
    from ..sites.rss import RSS

    claim = VerifiedCreator.objects.filter(pk=claim_id).first()
    user = User.objects.filter(pk=user_id).first()
    if not claim or not user:
        logger.warning(
            f"verify_creator_task: missing claim {claim_id} / user {user_id}"
        )
        return
    item = claim.item
    feed_url = getattr(item, "feed_url", None)
    if not feed_url:
        _fail_claim(claim, VerifiedCreator.FailureReason.NO_FEED)
        return
    try:
        feed, _etag, _last_modified, status = RSS.fetch_feed_with_metadata(feed_url)
    except Exception:
        # fetch_feed_with_metadata handles network errors itself; this keeps
        # any unexpected error from leaving the claim pending forever
        logger.exception(f"verify_creator_task: error fetching {feed_url}")
        feed, status = None, 0
    if status != 200 or feed is None:
        _fail_claim(claim, VerifiedCreator.FailureReason.FETCH_FAILED)
        return
    if not _has_audio_episode(feed):
        _fail_claim(claim, VerifiedCreator.FailureReason.NO_AUDIO)
        return
    # never let an unexpected matching/resolution/db error leave the claim stuck
    # in PENDING; fail it instead so the user can retry
    try:
        matched = _match_creator(user, feed)
        if matched:
            _verify_claim(claim, user, matched)
        else:
            _fail_claim(claim, VerifiedCreator.FailureReason.NO_MATCH)
    except Exception:
        logger.exception(f"verify_creator_task: error verifying claim {claim_id}")
        _fail_claim(claim, VerifiedCreator.FailureReason.FETCH_FAILED)
