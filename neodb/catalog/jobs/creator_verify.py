import django_rq
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


def verify_creator_task(claim_id: int, user_id: int) -> None:
    from ..sites.rss import RSS

    claim = VerifiedCreator.objects.filter(pk=claim_id).first()
    user = User.objects.filter(pk=user_id).first()
    if not claim or not user:
        logger.warning(f"verify_creator_task: missing claim {claim_id} / user {user_id}")
        return
    item = claim.item
    feed_url = getattr(item, "feed_url", None)
    if not feed_url:
        claim.state = VerifiedCreator.State.FAILED
        claim.failure_reason = VerifiedCreator.FailureReason.NO_FEED
        claim.save()
        return
    feed, _etag, _last_modified, status = RSS.fetch_feed_with_metadata(feed_url)
    if status != 200 or feed is None:
        claim.state = VerifiedCreator.State.FAILED
        claim.failure_reason = VerifiedCreator.FailureReason.FETCH_FAILED
        claim.save()
        return
    description = feed.get("description") or ""
    matched = match_creator_identity(
        [description, html_to_text(description)],
        creator_identity_candidates(user),
    )
    if matched:
        claim.state = VerifiedCreator.State.VERIFIED
        claim.matched = matched
        claim.failure_reason = ""
        claim.save()
        item.log_action({"!creator_verified": ["", f"{claim.owner} ({matched})"]})
        logger.info(f"creator verification matched {matched} for {claim}")
    else:
        claim.state = VerifiedCreator.State.FAILED
        claim.failure_reason = VerifiedCreator.FailureReason.NO_MATCH
        claim.save()
