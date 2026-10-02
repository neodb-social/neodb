from django.contrib.auth.decorators import login_required
from django.db.models import prefetch_related_objects
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from common.utils import CustomPaginator, PageLinksGenerator, get_uuid_or_404
from journal.models import Rating

from ..models import Item, RecommendationDismissal
from ..recommendation import dismiss_item, restore_item


def _prepare_cards(items: list[Item]) -> None:
    prefetch_related_objects(items, Item.credits_prefetch())
    Rating.attach_to_items(items)
    Item.attach_localized_credit_names(items)


@login_required
@require_http_methods(["POST"])
def dismiss_recommendation(request, item_uuid: str):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    dismiss_item(request.user, item)
    return render(request, "_reco_dismissed_body.html", {"item": item})


@login_required
@require_http_methods(["POST"])
def restore_recommendation(request, item_uuid: str):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    restore_item(request.user, item)
    if request.POST.get("remove"):
        return HttpResponse()
    _prepare_cards([item])
    return render(
        request, "_item_cover_card_body.html", {"item": item, "reco_dismiss": True}
    )


@login_required
def hidden_recommendations(request):
    qs = RecommendationDismissal.objects.filter(user=request.user).order_by(
        "-created_time"
    )
    paginator = CustomPaginator(qs, request)
    page_number = request.GET.get("page", default=1)
    page = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    # load through Item.objects to get the polymorphic subclass rows
    ids = [d.item_id for d in page.object_list]
    by_id = {i.pk: i for i in Item.objects.filter(pk__in=ids)}
    items = [by_id[i] for i in ids if i in by_id]
    if items:
        _prepare_cards(items)
    return render(
        request,
        "hidden_recommendations.html",
        {"items": items, "pagination": pagination},
    )
