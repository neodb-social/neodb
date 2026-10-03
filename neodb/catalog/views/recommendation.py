from django.contrib.auth.decorators import login_required
from django.db.models import prefetch_related_objects
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from django.utils.translation import gettext as _

from common.utils import get_uuid_or_404
from journal.models import Rating

from ..models import Item, RecommendationDismissal
from ..recommendation import dismiss_item, restore_item
from .view import _discover_list_page, prepare_list_items


def _prepare_cards(items: list[Item]) -> None:
    prefetch_related_objects(items, Item.credits_prefetch())
    Rating.attach_to_items(items)
    Item.attach_localized_credit_names(items)


def _is_list(request) -> bool:
    return request.POST.get("layout") == "list"


@login_required
@require_http_methods(["POST"])
def dismiss_recommendation(request, item_uuid: str):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    dismiss_item(request.user, item)
    template = (
        "_reco_dismissed_row.html" if _is_list(request) else "_reco_dismissed_body.html"
    )
    return render(request, template, {"item": item})


@login_required
@require_http_methods(["POST"])
def restore_recommendation(request, item_uuid: str):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    restore_item(request.user, item)
    if request.POST.get("remove"):
        return HttpResponse()
    if _is_list(request):
        prepare_list_items(request, [item])
        return render(request, "_list_item.html", {"item": item, "reco_dismiss": True})
    _prepare_cards([item])
    return render(
        request, "_item_cover_card_body.html", {"item": item, "reco_dismiss": True}
    )


@login_required
def hidden_recommendations(request):
    ids = list(
        RecommendationDismissal.objects.filter(user=request.user)
        .order_by("-created_time")
        .values_list("item_id", flat=True)
    )
    # load through Item.objects to get the polymorphic subclass rows
    by_id = {i.pk: i for i in Item.objects.filter(pk__in=ids)}
    return _discover_list_page(
        request,
        [by_id[i] for i in ids if i in by_id],
        _("Hidden recommendations"),
        _("These items are not recommended to you. Restore one to let it come back."),
        reco_restore=True,
    )
