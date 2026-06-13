import django_rq
from django.utils import timezone
from loguru import logger

from journal.models.renderers import html_to_text
from users.models import User

from ..models import (
    VerifiedCreator,
    creator_identity_candidates,
    match_creator_identity,
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
    description = feed.get("description") or ""
    matched = match_creator_identity(
        [description, html_to_text(description)],
        creator_identity_candidates(user),
    )
    if matched:
        verified = VerifiedCreator.objects.filter(
            pk=claim.pk, state=VerifiedCreator.State.PENDING
        ).update(
            state=VerifiedCreator.State.VERIFIED,
            matched=matched,
            failure_reason="",
            edited_time=timezone.now(),
        )
        if verified:
            item.log_action({"!creator_verified": ["", f"{claim.owner} ({matched})"]})
            logger.info(f"creator verification matched {matched} for {claim}")
    else:
        _fail_claim(claim, VerifiedCreator.FailureReason.NO_MATCH)
