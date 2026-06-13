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
from ..models import Item, VerifiedCreator, creator_identity_candidates

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
    return {
        "item": item,
        "my_claim": item.verified_creators.filter(owner=request.user.identity).first(),
        "verified_creators": item.verified_creator_list,
        "candidates": creator_identity_candidates(request.user),
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
    claim, created = VerifiedCreator.objects.get_or_create(
        item=item, owner=request.user.identity
    )
    if claim.state == VerifiedCreator.State.VERIFIED:
        messages.add_message(
            request, messages.INFO, _("You are already a verified creator.")
        )
        return redirect(_verify_page_url(item))
    if claim.state == VerifiedCreator.State.PENDING and not created:
        messages.add_message(
            request, messages.INFO, _("Verification is already in progress.")
        )
        return redirect(_verify_page_url(item))
    # short cooldown so repeated submissions can't flood the job queue
    lock_key = f"_verify_creator_lock:{item.pk}:{request.user.identity.pk}"
    if not cache.add(lock_key, 1, timeout=VERIFY_COOLDOWN_SECONDS):
        messages.add_message(
            request, messages.WARNING, _("Please wait a moment before trying again.")
        )
        return redirect(_verify_page_url(item))
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
    claim = get_object_or_404(
        VerifiedCreator, pk=request.POST.get("claim_id"), item=item
    )
    if claim.owner_id != request.user.identity.pk and not request.user.is_superuser:
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
