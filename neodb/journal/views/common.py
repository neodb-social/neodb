import uuid
from functools import wraps

import filetype
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import F, Min, OuterRef, Subquery
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import last_modified, require_http_methods

from catalog.models import Item, ItemCategory, PeopleType
from common.models.misc import int_
from common.utils import (
    AuthedHttpRequest,
    CustomPaginator,
    PageLinksGenerator,
    get_uuid_or_404,
    target_identity_required,
)

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


def post_quotes_count(post) -> int:
    """Count active quote-posts for ``post``.

    Mirrors the count exposed in ``single_post.html`` so article / review /
    collection footers can render a metrics chip next to the Quote link
    (parity with ``post.stats.replies``; Takahe does not store a ``quotes``
    stat).
    """
    if post is None:
        return 0
    from takahe.models import Post

    return (
        Post.objects.filter(quote_url=post.object_uri)
        .exclude(state__in=["deleted", "deleted_fanned_out"])
        .count()
    )


def conditional_get_for_anonymous(get_timestamp):
    """Apply HTTP conditional-GET (``Last-Modified``) for anonymous viewers.

    ``get_timestamp(request, *args, **kwargs) -> datetime | None`` is called
    before the view; returning ``None`` skips the conditional path entirely
    (so the view runs as usual). The callback is responsible for any
    visibility / identity gates that aren't already reflected in the
    returned timestamp — anything the callback can't safely answer should
    return ``None`` so the view body runs and renders the real
    403/404/redirect.

    Authenticated requests always bypass: their response varies on viewer
    identity, which a single ``Last-Modified`` value cannot represent
    safely.

    Sets ``Cache-Control: private, max-age=0, must-revalidate`` and adds
    ``Cookie``/``Accept`` to ``Vary`` on **both** 200 and 304 responses,
    so shared caches don't serve an anonymous body to a logged-in user
    and browsers always revalidate (yielding 304 when unchanged).
    """

    def _lm(request, *args, **kwargs):
        if request.user.is_authenticated:
            return None
        return get_timestamp(request, *args, **kwargs)

    def decorator(view):
        conditional = last_modified(_lm)(view)

        @wraps(view)
        def wrapped(request, *args, **kwargs):
            # The inner ``conditional`` may short-circuit to 304 without
            # running ``view``; patch headers on whatever response comes
            # back so 304s carry Cache-Control/Vary too.
            response = conditional(request, *args, **kwargs)
            patch_cache_control(response, private=True, max_age=0, must_revalidate=True)
            patch_vary_headers(response, ("Cookie", "Accept"))
            return response

        return wrapped

    return decorator


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
        queryset = target.shelf_manager.get_members(shelf_type)
    elif type == "tagmember":
        tag = Tag.objects.filter(owner=target, title=tag_title).first()
        if not tag:
            return render_list_not_found(request)
        if tag.visibility != 0 and target != viewer:
            return render_list_not_found(request)
        queryset = TagMember.objects.filter(parent=tag)
    elif type == "review" and item_category:
        queryset = Review.objects.all()
    else:
        raise BadRequest(_("Invalid parameter"))
    queryset = queryset.filter(q_owned_piece_visible_to_user(request.user, target))
    # year dropdown range is per user, computed before the item_category filter
    # so the aggregate skips the expensive catalog_item join
    start_date = queryset.aggregate(Min("created_time"))["created_time__min"]
    if start_date:
        start_year = start_date.year
        current_year = timezone.now().year
        years = range(current_year, start_year - 1, -1)
    else:
        years = []
    if item_category:
        queryset = queryset.filter(q_item_in_category(item_category))
    if sort == "rating":
        rating = Rating.objects.filter(
            owner_id=OuterRef("owner_id"), item_id=OuterRef("item_id")
        )
        queryset = queryset.alias(
            rating_grade=Subquery(rating.values("grade"))
        ).order_by(F("rating_grade").desc(nulls_last=True), "id")
    else:
        queryset = queryset.order_by("-created_time")
    if year:
        year = int(year)
        queryset = queryset.filter(created_time__year=year)
    people_type = request.GET.get("people_type") or ""
    if people_type in PeopleType.values and item_category == ItemCategory.People:
        queryset = queryset.filter(item__people__people_type=people_type)
    # Slim external_resources prefetch: cards skip the metadata JSON (EGGPLANT-1DX).
    queryset = queryset.prefetch_related(
        "item", Item.external_resources_prefetch(lookup="item__external_resources")
    )
    paginator = CustomPaginator(queryset, request)
    page_number = int_(request.GET.get("page", default=1))
    members = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    # Batch-fetch marks and rating info for all items on this page to avoid N+1 queries
    items = [m.item for m in members]
    if items:
        Item.prefetch_parent_items(items)
        Item.prefetch_credits(items)
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
    return_url = request.GET.get("return_url") or ""
    if not url_has_allowed_host_and_scheme(
        return_url,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        return_url = "/"
    if not piece.is_deletable_by(request.user):
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
