from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import BadRequest, PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.utils import (
    get_uuid_or_404,
    target_identity_required,
    user_identity_required,
)
from users.models import APIdentity

from ..jobs.creator_verify import enqueue_creator_verification
from ..models import (
    Item,
    VerifiedCreator,
    creator_identity_candidates,
    user_controls_owner,
    user_owned_claims_q,
)

VERIFY_COOLDOWN_SECONDS = 60


def _get_verifiable_item(item_uuid) -> Item:
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.class_name != "podcast":
        raise BadRequest(
            _("Creator verification is only available for podcasts for now.")
        )
    return item


def _verify_page_url(item: Item) -> str:
    return reverse("catalog:verify_creator", args=[item.url_path, item.uuid])


def _verify_context(request, item: Item) -> dict:
    # a verified claim may be attributed to the user's linked Mastodon identity,
    # so look it up by any identity the user controls, not just the local one
    my_claim = (
        item.verified_creators.filter(user_owned_claims_q(request.user))
        .select_related("owner")
        .order_by("-edited_time")
        .first()
    )
    # show only link/url identifiers; bare @handles still verify but are
    # discouraged in the UI in favor of rel="me" links
    candidates = [c for c in creator_identity_candidates(request.user) if "://" in c]
    # a url to show in the copyable rel="me" example, preferring the linked
    # Mastodon profile when available
    mastodon = request.user.mastodon
    example_url = (mastodon.url if mastodon and mastodon.url else "") or (
        candidates[0] if candidates else ""
    )
    return {
        "item": item,
        "my_claim": my_claim,
        "verified_creators": item.verified_creator_list,
        "candidates": candidates,
        "example_url": example_url,
    }


@require_http_methods(["GET"])
@login_required
@user_identity_required
def verify_creator(request, item_path, item_uuid):
    item = _get_verifiable_item(item_uuid)
    return render(request, "catalog_verify.html", _verify_context(request, item))


@require_http_methods(["GET"])
@login_required
@user_identity_required
def verify_creator_status(request, item_path, item_uuid):
    item = _get_verifiable_item(item_uuid)
    context = _verify_context(request, item)
    response = render(request, "_verify_status.html", context)
    claim = context["my_claim"]
    if claim and claim.state != VerifiedCreator.State.PENDING:
        # reload the page when polling concludes, so the verified creators
        # list and edit lock indicators reflect the new state
        response["HX-Refresh"] = "true"
    return response


@require_http_methods(["POST"])
@login_required
@user_identity_required
def verify_creator_start(request, item_path, item_uuid):
    item = _get_verifiable_item(item_uuid)
    if not getattr(item, "feed_url", None):
        messages.add_message(
            request, messages.ERROR, _("No feed url available for this item.")
        )
        return redirect(_verify_page_url(item))
    # a prior verification may have re-homed the claim onto a linked identity,
    # so check every identity the user controls for an existing verified claim
    if VerifiedCreator.objects.filter(
        user_owned_claims_q(request.user),
        item=item,
        state=VerifiedCreator.State.VERIFIED,
    ).exists():
        messages.add_message(
            request, messages.INFO, _("You are already a verified creator.")
        )
        return redirect(_verify_page_url(item))
    # a pending claim is always owned by the local identity (attribution is
    # only resolved once verification succeeds)
    existing = VerifiedCreator.objects.filter(
        item=item, owner=request.user.identity
    ).first()
    if existing and existing.state == VerifiedCreator.State.PENDING:
        messages.add_message(
            request, messages.INFO, _("Verification is already in progress.")
        )
        return redirect(_verify_page_url(item))
    # acquire a short cooldown lock before creating/mutating the claim, so a
    # blocked submission can't leave an orphaned PENDING claim with no job
    lock_key = f"_verify_creator_lock:{item.pk}:{request.user.identity.pk}"
    if not cache.add(lock_key, 1, timeout=VERIFY_COOLDOWN_SECONDS):
        messages.add_message(
            request, messages.WARNING, _("Please wait a moment before trying again.")
        )
        return redirect(_verify_page_url(item))
    claim, _created = VerifiedCreator.objects.get_or_create(
        item=item, owner=request.user.identity
    )
    claim.state = VerifiedCreator.State.PENDING
    claim.matched = ""
    claim.failure_reason = ""
    claim.save()
    enqueue_creator_verification(claim, request.user)
    return redirect(_verify_page_url(item))


@require_http_methods(["POST"])
@login_required
@user_identity_required
def verify_creator_manual(request, item_path, item_uuid):
    if not request.user.is_superuser:
        raise PermissionDenied(_("Insufficient permission"))
    item = _get_verifiable_item(item_uuid)
    handle = request.POST.get("handle", "").strip()
    try:
        identity = APIdentity.get_by_handle(handle)
    except APIdentity.DoesNotExist:
        messages.add_message(request, messages.ERROR, _("User not found"))
        return redirect(_verify_page_url(item))
    VerifiedCreator.objects.update_or_create(
        item=item,
        owner=identity,
        defaults={
            "state": VerifiedCreator.State.VERIFIED,
            "matched": "manual",
            "failure_reason": "",
        },
    )
    item.log_action(
        {"!creator_verified": ["", f"{identity} (manual by @{request.user.username})"]}
    )
    messages.add_message(request, messages.INFO, _("Creator verified."))
    return redirect(_verify_page_url(item))


@require_http_methods(["POST"])
@login_required
@user_identity_required
def unverify_creator(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    claim_id = request.POST.get("claim_id", "")
    if not claim_id.isdigit():
        raise BadRequest(_("Invalid parameter"))
    claim = get_object_or_404(
        VerifiedCreator.objects.select_related("owner"),
        pk=claim_id,
        item=item,
    )
    if not user_controls_owner(request.user, claim.owner) and (
        not request.user.is_superuser
    ):
        raise PermissionDenied(_("Insufficient permission"))
    item.log_action({"!creator_unverified": ["", str(claim.owner)]})
    claim.delete()
    messages.add_message(request, messages.INFO, _("Creator verification removed."))
    return redirect(item.url)


@require_http_methods(["GET"])
@target_identity_required
def user_verified_works(request, user_name):
    target = request.target_identity
    item_ids = list(
        target.verified_works.filter(state=VerifiedCreator.State.VERIFIED)
        .order_by("-created_time")
        .values_list("item_id", flat=True)
    )
    # query via polymorphic manager so subclass templates resolve correctly
    items_by_id = {
        i.pk: i
        for i in Item.objects.filter(
            pk__in=item_ids, is_deleted=False, merged_to_item__isnull=True
        )
    }
    items = [items_by_id[i] for i in item_ids if i in items_by_id]
    return render(
        request,
        "user_verified_works.html",
        {"identity": target, "items": items},
    )
