import datetime
import uuid

import filetype
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import F, Min, OuterRef, Subquery
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import Item, ItemCategory, PeopleType
from common.models.misc import int_
from common.utils import (
    AuthedHttpRequest,
    CustomPaginator,
    PageLinksGenerator,
    get_uuid_or_404,
    target_identity_required,
)
from common.validators import get_safe_redirect_url

from ..models import (
    Mark,
    Piece,
    Rating,
    Review,
    ShelfManager,
    ShelfType,
    Tag,
    TagMember,
    q_item_in_category,
    q_owned_piece_visible_to_user,
)

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB


def generate_upload_path(identity_id: int | str, ext: str) -> str:
    """Generate a storage-relative upload path: upload/<identity_id>/<year>/<uuid>.<ext>"""
    year = timezone.now().strftime("%Y")
    filename = f"{uuid.uuid4()}.{ext}"
    return f"upload/{identity_id}/{year}/{filename}"


@login_required
@require_http_methods(["POST"])
def upload_image(request: AuthedHttpRequest) -> JsonResponse:
    image = request.FILES.get("image")
    if not image:
        return JsonResponse({"error": "No image provided"}, status=400)
    if image.size and image.size > _MAX_IMAGE_SIZE:
        return JsonResponse({"error": "Image too large (max 10MB)"}, status=400)
    kind = filetype.guess(image)
    if not kind or kind.mime not in _ALLOWED_IMAGE_TYPES:
        return JsonResponse({"error": "Unsupported image type"}, status=400)
    image.seek(0)
    rel_path = generate_upload_path(request.user.identity.pk, kind.extension)
    saved_path = default_storage.save(rel_path, ContentFile(image.read()))
    url = default_storage.url(saved_path)
    return JsonResponse({"data": {"filePath": url}})


PAGE_SIZE = 10


def render_relogin(request):
    return render(
        request,
        "common/error.html",
        {
            "url": reverse("mastodon:login")
            + "?domain="
            + request.user.mastodon.domain,
            "msg": _("Data saved but unable to crosspost to Fediverse instance."),
            "secondary_msg": _(
                "Redirecting to your Fediverse instance now to re-authenticate."
            ),
        },
    )


def render_list_not_found(request):
    msg = _("List not found.")
    return render(
        request,
        "common/error.html",
        {
            "msg": msg,
        },
    )


@target_identity_required
def render_list(
    request: AuthedHttpRequest,
    user_name,
    type,
    shelf_type: ShelfType | None = None,
    item_category=None,
    tag_title=None,
    year=None,
    sort="time",
):
    target = request.target_identity
    viewer = request.identity
    tag = None
    sort = request.GET.get("sort")
    year = request.GET.get("year")
    if type == "mark" and shelf_type:
        queryset = target.shelf_manager.get_members(shelf_type, item_category)
    elif type == "tagmember":
        tag = Tag.objects.filter(owner=target, title=tag_title).first()
        if not tag:
            return render_list_not_found(request)
        if tag.visibility != 0 and target != viewer:
            return render_list_not_found(request)
        queryset = TagMember.objects.filter(parent=tag)
    elif type == "review" and item_category:
        queryset = Review.objects.filter(q_item_in_category(item_category))
    else:
        raise BadRequest(_("Invalid parameter"))
    if sort == "rating":
        rating = Rating.objects.filter(
            owner_id=OuterRef("owner_id"), item_id=OuterRef("item_id")
        )
        queryset = queryset.alias(
            rating_grade=Subquery(rating.values("grade"))
        ).order_by(F("rating_grade").desc(nulls_last=True), "id")
    else:
        queryset = queryset.order_by("-created_time")
    start_date = queryset.aggregate(Min("created_time"))["created_time__min"]
    if start_date:
        start_year = start_date.year
        current_year = datetime.datetime.now().year
        years = reversed(range(start_year, current_year + 1))
    else:
        years = []
    queryset = queryset.filter(q_owned_piece_visible_to_user(request.user, target))
    if year:
        year = int(year)
        queryset = queryset.filter(created_time__year=year)
    people_type = request.GET.get("people_type") or ""
    if people_type in PeopleType.values and item_category == ItemCategory.People:
        queryset = queryset.filter(item__people__people_type=people_type)
    queryset = queryset.prefetch_related("item", "item__external_resources")
    paginator = CustomPaginator(queryset, request)
    page_number = int_(request.GET.get("page", default=1))
    members = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    # Batch-fetch marks and rating info for all items on this page to avoid N+1 queries
    items = [m.item for m in members]
    if items:
        Item.prefetch_parent_items(items)
        Rating.attach_to_items(items)
        marks = Mark.get_marks_by_items(target, items, request.user)
        for m in members:
            m.__dict__["mark"] = marks.get(m.item_id) or Mark(target, m.item)
    shelf_labels = (
        ShelfManager.get_labels_for_category(item_category) if item_category else []
    )
    return render(
        request,
        f"user_{type}_list.html",
        {
            "user": target.user,
            "identity": target,
            "members": members,
            "tag": tag,
            "pagination": pagination,
            "years": years,
            "year": year,
            "sort": sort,
            "shelf": shelf_type,
            "shelf_labels": shelf_labels,
            "category": item_category,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def piece_delete(request, piece_uuid):
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    return_url = get_safe_redirect_url(request.GET.get("return_url"), "/")
    if not piece.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if request.method == "GET":
        return render(
            request, "piece_delete.html", {"piece": piece, "return_url": return_url}
        )
    piece.delete()
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = return_url
        return response
    return redirect(return_url)
