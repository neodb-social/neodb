from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.utils import get_uuid_or_404

from ..models import Item, RecommendationDismissal
from ..recommendation import dismiss_item, restore_item
from .view import _discover_list_page, _prepare_reco_cards, prepare_list_items


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
    _prepare_reco_cards([item])
    return render(
        request, "_item_cover_card_body.html", {"item": item, "reco_dismiss": True}
    )


def _items_in_order(ids) -> list[Item]:
    # through Item.objects to get the polymorphic subclass rows
    by_id = {i.pk: i for i in Item.objects.filter(pk__in=list(ids))}
    return [by_id[i] for i in ids if i in by_id]


@login_required
def hidden_recommendations(request):
    return _discover_list_page(
        request,
        RecommendationDismissal.objects.filter(user=request.user)
        .order_by("-created_time")
        .values_list("item_id", flat=True),
        _("Hidden recommendations"),
        _("These items are not recommended to you. Restore one to let it come back."),
        to_items=_items_in_order,
        reco_restore=True,
    )
