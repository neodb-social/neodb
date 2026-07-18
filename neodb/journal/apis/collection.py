from datetime import datetime
from typing import Any, List

from django import forms
from django.conf import settings
from django.core.cache import cache
from django.core.signing import b62_encode
from django.db.models import Count, QuerySet, prefetch_related_objects
from django.http import Http404, HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.cache import cache_page
from ninja import Field, File, Schema, Status
from ninja.decorators import decorate_view
from ninja.errors import HttpError
from ninja.files import UploadedFile
from ninja.pagination import paginate

from catalog.models import Item, ItemSchema
from common.api import (
    OptionalOAuthAccessTokenAuth,
    PageNumberPagination,
    RedirectedResult,
    Result,
    api,
)
from common.sentry import record_activity
from journal.models.collection import COVER_MAX_BYTES
from journal.models.common import (
    q_owned_piece_visible_to_user,
    q_piece_visible_to_user,
)
from takahe.utils import Takahe
from users.apis import UserIdentitySchema
from users.models.apidentity import APIdentity

from ..models import (
    Collection,
    CollectionMember,
    FeaturedCollection,
    Rating,
    ShelfMember,
    ShelfType,
)


class CollectionPageNumberPagination(PageNumberPagination):
    """Pagination that batch-attaches ``item_count_by_category`` after slicing.

    Plain ``PageNumberPagination`` would serialize each Collection one at a
    time, each triggering its own ``get_summary()``; this hook fetches all
    static collections' member→category counts in a single query.
    """

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: PageNumberPagination.Input,
        request: HttpRequest,
        **params: Any,
    ):
        val = super().paginate_queryset(queryset, pagination, request, **params)
        if isinstance(val, tuple):
            return val
        data = val.get("data") if isinstance(val, dict) else None
        if data:
            collections = list(data)
            Collection.attach_item_count_by_category(collections)
            _prefetch_collection_owners(collections)
        return val


def _prefetch_collection_owners(collections: List[Collection]) -> None:
    """Batch-load takahe identities so owner (``UserIdentitySchema``)
    serialization (display_name/avatar) does not fire a cross-db query per
    collection.

    Querysets feeding this should ``select_related("owner")``.
    """
    Takahe.prefetch_takahe_identities([c.owner for c in collections])


class CollectionSchema(Schema):
    uuid: str
    url: str
    api_url: str
    visibility: int = Field(ge=0, le=2)
    post_id: int | None = Field(alias="latest_post_id")
    created_time: datetime
    title: str
    brief: str
    cover_image_url: str | None
    cover: str = Field(deprecated=True)
    html_content: str
    is_dynamic: bool
    query: str | None = None
    item_count_by_category: dict[str, int]
    owner: UserIdentitySchema


class CollectionInSchema(Schema):
    title: str
    brief: str
    visibility: int = Field(ge=0, le=2)
    query: str | None = None


class CollectionItemSchema(Schema):
    item: ItemSchema
    note: str

    @staticmethod
    def resolve_note(obj: "CollectionMember | dict[str, Any]") -> str:
        # CollectionMember.note is nullable; keep the API contract a plain str
        note = obj.get("note") if isinstance(obj, dict) else obj.note
        return note if isinstance(note, str) else ""


def _prefetch_collection_member_items(data: list) -> None:
    """Batch-hydrate items for ``CollectionItemSchema`` (``item: ItemSchema``).

    Without this, each member serializes its item's ``external_resources`` and
    ``credits`` one row at a time. Dynamic collections carry the item in a
    dict; static members reference it through a polymorphic FK that can't be
    ``select_related`` (django-polymorphic), so resolve those in a single query
    and assign back, mirroring ``Collection.get_members_by_page``.
    """
    if not data:
        return
    members = [m for m in data if not isinstance(m, dict)]
    item_ids = [m.item_id for m in members if m.item_id]
    if item_ids:
        items_map = {it.pk: it for it in Item.objects.filter(pk__in=item_ids)}
        for m in members:
            # Assign only when resolved. The FK is PROTECTed so a miss is not
            # expected; dereferencing m.item for a missing id would defeat the
            # batch with a lazy load (and assigning None would break the schema).
            resolved = items_map.get(m.item_id)
            if resolved is not None:
                m.item = resolved
    items = [(m["item"] if isinstance(m, dict) else m.item) for m in data]
    items = [i for i in items if i is not None]
    if not items:
        return
    # external_resources skips the metadata JSON (EGGPLANT-1DX).
    prefetch_related_objects(
        items,
        Item.external_resources_prefetch(),
        Item.credits_prefetch(),
    )
    Item.prefetch_parent_items(items)
    Item.prefetch_edition_works(items)
    Rating.attach_to_items(items)


class CollectionItemPageNumberPagination(PageNumberPagination):
    """Hydrate the page's items so ``CollectionItemSchema`` serialization does
    not fire per-item ``external_resources``/``credits`` queries (N+1)."""

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: PageNumberPagination.Input,
        request: HttpRequest,
        **params: Any,
    ):
        val = super().paginate_queryset(queryset, pagination, request, **params)
        if isinstance(val, tuple):
            return val
        data = val.get("data") if isinstance(val, dict) else None
        if data:
            _prefetch_collection_member_items(list(data))
        return val


class CollectionItemInSchema(Schema):
    item_uuid: str
    note: str


class CollectionItemNoteInSchema(Schema):
    note: str


class CollectionItemReorderInSchema(Schema):
    item_uuids: list[str]


class FeaturedCollectionStatsSchema(Schema):
    wishlist: int
    progress: int
    complete: int
    dropped: int
    total: int


@api.get(
    "/me/collection/",
    response={200: List[CollectionSchema], 401: Result, 403: Result},
    tags=["collection"],
)
@paginate(CollectionPageNumberPagination)
def list_user_collections(request):
    """
    Get collections created by current user
    """
    queryset = Collection.objects.filter(owner=request.user.identity).select_related(
        "owner"
    )
    return queryset


@api.get(
    "/user/{handle}/collection/",
    response={200: List[CollectionSchema], 401: Result, 404: Result},
    tags=["collection"],
    auth=OptionalOAuthAccessTokenAuth(),
)
@paginate(CollectionPageNumberPagination)
def list_collections_of_user(request, handle: str):
    """
    Get collections created by a specific user

    Only collections visible to the requesting identity are returned;
    anonymous access is allowed if the user's profile is anonymous viewable.
    """
    try:
        target = APIdentity.get_by_handle(handle)
    except APIdentity.DoesNotExist:
        raise Http404("User not found")
    qv = q_owned_piece_visible_to_user(request.user, target, check_blocking=True)
    return (
        Collection.objects.filter(qv).select_related("owner").order_by("-edited_time")
    )


@api.get(
    "/user/{handle}/collection/liked/",
    response={200: List[CollectionSchema], 401: Result, 404: Result},
    tags=["collection"],
    auth=OptionalOAuthAccessTokenAuth(),
)
@paginate(CollectionPageNumberPagination)
def list_liked_collections_of_user(request, handle: str):
    """
    Get collections liked by a specific user

    Only collections visible to the requesting identity are returned;
    anonymous access is allowed if the user's profile is anonymous viewable.
    """
    try:
        target = APIdentity.get_by_handle(handle)
    except APIdentity.DoesNotExist:
        raise Http404("User not found")
    viewer = request.user.identity if request.user.is_authenticated else None
    # gate on the target like the created-collections endpoint above:
    # hide the list rather than 403, so it's indistinguishable from empty
    if target.restricted:
        return Collection.objects.none()
    if viewer is None and not target.anonymous_viewable:
        return Collection.objects.none()
    if viewer and viewer != target and viewer.is_blocked_by(target):
        return Collection.objects.none()
    queryset = Collection.objects.filter(
        interactions__identity=target,
        interactions__interaction_type="like",
        interactions__target_type="Collection",
    )
    if viewer != target:
        queryset = queryset.filter(q_piece_visible_to_user(request.user))
    return queryset.select_related("owner").order_by("-edited_time")


@api.get(
    "/me/collection/{collection_uuid}",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def get_user_collection(request, collection_uuid: str):
    """
    Get collections by its uuid
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if c.owner != request.user.identity and not c.is_editable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    return c


@api.get(
    "/collection/{collection_uuid}",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
    auth=OptionalOAuthAccessTokenAuth(),
)
def get_collection(request, collection_uuid: str):
    """
    Get details of a collection
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_visible_to(request.user):
        return Status(403, {"message": "Permission denied"})
    return c


@api.get(
    "/collection/{collection_uuid}/item/",
    response={200: List[CollectionItemSchema], 401: Result, 403: Result, 404: Result},
    tags=["collection"],
    auth=OptionalOAuthAccessTokenAuth(),
)
@paginate(CollectionItemPageNumberPagination)
def collection_list_items(request, collection_uuid: str):
    """
    Get items in a collection collections
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        raise Http404("Collection not found")
    if not c.is_visible_to(request.user):
        raise HttpError(403, "Permission denied")
    if c.is_dynamic:
        items = c.query_result.items if c.query_result else []
        members = [{"item": i, "note": ""} for i in items]
        return members
    else:
        return c.ordered_members


@api.post(
    "/me/collection/",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def create_collection(request, c_in: CollectionInSchema):
    """
    Create collection.

    `title`, `brief` (markdown formatted) and `visibility` are required;
    """
    q = (c_in.query or "").strip() or None
    c = Collection(
        owner=request.user.identity,
        title=c_in.title,
        brief=c_in.brief,
        visibility=c_in.visibility,
        query=q,
    )
    c.application_id_when_save = getattr(request, "application_id", None)
    c.save()
    record_activity("collection", "api")
    return c


@api.put(
    "/me/collection/{collection_uuid}",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def update_collection(request, collection_uuid: str, c_in: CollectionInSchema):
    """
    Update collection.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_editable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    q = (c_in.query or "").strip() or None
    is_dynamic = bool(q)
    if c.is_dynamic != is_dynamic:
        return Status(403, {"message": "Cannot change collection type"})
    if c.owner != request.user.identity and (
        c_in.visibility != c.visibility or q != c.query
    ):
        return Status(403, {"message": "Only owner can change visibility or query"})
    c.title = c_in.title
    c.brief = c_in.brief
    c.visibility = c_in.visibility
    c.query = q
    c.application_id_when_save = getattr(request, "application_id", None)
    c.save()
    record_activity("collection", "api")
    return c


@api.put(
    "/me/collection/{collection_uuid}/cover",
    response={
        200: CollectionSchema,
        400: Result,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["collection"],
)
def collection_set_cover(request, collection_uuid: str, cover: File[UploadedFile]):
    """
    Set the cover image of a collection.

    Send the image as `multipart/form-data` with a `cover` file field;
    it must be a valid image no larger than 5MB.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_editable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    if (cover.size or 0) > COVER_MAX_BYTES:
        return Status(400, {"message": "Image file too large"})
    try:
        f = forms.ImageField().to_python(cover)
    except forms.ValidationError:
        return Status(400, {"message": "Invalid image file"})
    if f is None:
        return Status(400, {"message": "Invalid image file"})
    # normalize the filename from the detected format so the stored path
    # gets a meaningful extension even for extension-less uploads
    image = getattr(f, "image", None)  # set by ImageField.to_python
    fmt = (image.format or "").lower() if image else ""
    ext = {"jpeg": "jpg"}.get(fmt, fmt)
    if ext:
        f.name = f"cover.{ext}"
    c.cover = f
    c.application_id_when_save = getattr(request, "application_id", None)
    c.save()
    record_activity("collection", "api")
    return c


@api.delete(
    "/me/collection/{collection_uuid}/cover",
    response={200: CollectionSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def collection_remove_cover(request, collection_uuid: str):
    """
    Reset the cover image of a collection to default.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_editable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    c.cover = settings.DEFAULT_ITEM_COVER
    c.application_id_when_save = getattr(request, "application_id", None)
    c.save()
    record_activity("collection", "api")
    return c


@api.delete(
    "/me/collection/{collection_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def delete_collection(request, collection_uuid: str):
    """
    Remove a collection.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_deletable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    c.delete()
    return Status(200, {"message": "OK"})


@api.get(
    "/me/collection/{collection_uuid}/item/",
    response={200: List[CollectionItemSchema], 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
@paginate(CollectionItemPageNumberPagination)
def user_collection_list_items(request, collection_uuid: str):
    """
    Get items in a collection collections
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        raise Http404("Collection not found")
    if c.owner != request.user.identity and not c.is_editable_by(request.user):
        raise HttpError(403, "Permission denied")
    if c.is_dynamic:
        items = c.query_result.items if c.query_result else []
        members = [{"item": i, "note": ""} for i in items]
        return members
    else:
        return c.ordered_members


@api.post(
    "/me/collection/{collection_uuid}/item/",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def collection_add_item(
    request, collection_uuid: str, collection_item: CollectionItemInSchema
):
    """
    Add an item to collection

    If the item is already in the collection this is a no-op and its note is
    kept; use `PUT /me/collection/{collection_uuid}/item/{item_uuid}` to
    update the note of an existing item.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_editable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    if c.is_dynamic:
        return Status(
            403, {"message": "Item list of dynamic collection cannot be updated"}
        )
    if not collection_item.item_uuid:
        return Status(404, {"message": "Item not found"})
    item = Item.get_by_url(collection_item.item_uuid)
    if not item:
        return Status(404, {"message": "Item not found"})
    c.append_item(item, note=collection_item.note)
    return Status(200, {"message": "OK"})


@api.put(
    "/me/collection/{collection_uuid}/item/{item_uuid}",
    response={200: CollectionItemSchema, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def collection_update_item(
    request,
    collection_uuid: str,
    item_uuid: str,
    collection_item: CollectionItemNoteInSchema,
):
    """
    Update the note of an item in the collection.

    The item must already be in the collection; 404 is returned otherwise
    (position is preserved, unlike remove + re-add). Set `note` to an empty
    string to clear it. Returns the updated member.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_editable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    if c.is_dynamic:
        return Status(
            403, {"message": "Item list of dynamic collection cannot be updated"}
        )
    item = Item.get_by_url(item_uuid)
    if not item:
        return Status(404, {"message": "Item not found"})
    member = c.update_item_note(item, collection_item.note)
    if not member:
        return Status(404, {"message": "Item not in collection"})
    return member


@api.post(
    "/me/collection/{collection_uuid}/reorder_items",
    response={200: Result, 400: Result, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def collection_reorder_items(
    request, collection_uuid: str, payload: CollectionItemReorderInSchema
):
    """
    Reorder items in a collection.

    `item_uuids` must contain the uuid of every item currently in the
    collection, in the desired order. Partial lists are rejected because
    they would leave the collection with conflicting positions.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_editable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    if c.is_dynamic:
        return Status(
            403, {"message": "Item list of dynamic collection cannot be updated"}
        )
    item_uuids = payload.item_uuids
    if len(item_uuids) != len(set(item_uuids)):
        return Status(400, {"message": "Duplicate item_uuids"})
    members_by_uuid = {
        b62_encode(uid.int).zfill(22): pk
        for pk, uid in c.members.values_list("pk", "item__uid")
    }
    if set(item_uuids) != set(members_by_uuid.keys()):
        return Status(
            400,
            {
                "message": "item_uuids must list every item in the collection exactly once"
            },
        )
    ordered_member_ids = [members_by_uuid[u] for u in item_uuids]
    c.update_member_order(ordered_member_ids)
    return Status(200, {"message": "OK"})


@api.delete(
    "/me/collection/{collection_uuid}/item/{item_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["collection"],
)
def collection_delete_item(request, collection_uuid: str, item_uuid: str):
    """
    Remove an item from collection
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_editable_by(request.user):
        return Status(403, {"message": "Permission denied"})
    if c.is_dynamic:
        return Status(
            403, {"message": "Item list of dynamic collection cannot be updated"}
        )
    item = Item.get_by_url(item_uuid)
    if not item:
        return Status(404, {"message": "Item not found"})
    c.remove_item(item)
    return Status(200, {"message": "OK"})


@api.get(
    "/item/{item_uuid}/collection/",
    response={200: List[CollectionSchema], 401: Result, 404: Result},
    tags=["collection"],
    auth=OptionalOAuthAccessTokenAuth(),
)
@paginate(CollectionPageNumberPagination)
def list_item_collections(request, item_uuid: str):
    """
    List collections containing the item
    """
    item = Item.get_by_url(item_uuid, resolve_merge=True)
    if not item or item.is_deleted:
        raise Http404("Item not found")
    qv = q_piece_visible_to_user(request.user)
    return (
        Collection.objects.filter(items=item)
        .filter(qv)
        .select_related("owner")
        .order_by("-created_time")
    )


@api.post(
    "/me/collection/featured/{collection_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["featured collection"],
)
def collection_set_featured(request, collection_uuid: str):
    """
    Set a collection as featured for current user.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_visible_to(request.user):
        return Status(403, {"message": "Permission denied"})
    FeaturedCollection.objects.update_or_create(owner=request.user.identity, target=c)
    return Status(200, {"message": "OK"})


@api.delete(
    "/me/collection/featured/{collection_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["featured collection"],
)
def collection_unset_featured(request, collection_uuid: str):
    """
    Unset a featured collection for current user.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not c.is_visible_to(request.user):
        return Status(403, {"message": "Permission denied"})
    FeaturedCollection.objects.filter(owner=request.user.identity, target=c).delete()
    return Status(200, {"message": "OK"})


@api.get(
    "/me/collection/featured/",
    response={200: list[CollectionSchema], 401: Result, 403: Result},
    tags=["featured collection"],
)
def list_featured_collections(request):
    """
    List featured collections for current user.
    """
    collections = list(
        Collection.objects.filter(featured_by=request.user.identity)
        .filter(q_piece_visible_to_user(request.user))
        .select_related("owner")
    )
    Collection.attach_item_count_by_category(collections)
    _prefetch_collection_owners(collections)
    return collections


@api.get(
    "/me/collection/featured/{collection_uuid}",
    response={302: RedirectedResult, 401: Result, 403: Result, 404: Result},
    tags=["featured collection"],
)
def get_featured_collection(request, collection_uuid: str, response: HttpResponse):
    """
    Redirect to featured collection details.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not FeaturedCollection.objects.filter(
        owner=request.user.identity, target=c
    ).exists():
        return Status(404, {"message": "Collection not found"})
    if not c.is_visible_to(request.user):
        return Status(403, {"message": "Permission denied"})
    response["Location"] = f"/api/collection/{c.uuid}"
    return Status(302, {"message": "OK", "url": c.api_url})


@api.get(
    "/me/collection/featured/{collection_uuid}/stats",
    response={
        200: FeaturedCollectionStatsSchema,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["featured collection"],
)
def get_featured_collection_stats(request, collection_uuid: str):
    """
    Get featured collection stats for current user.
    """
    c = Collection.get_by_url(collection_uuid)
    if not c:
        return Status(404, {"message": "Collection not found"})
    if not FeaturedCollection.objects.filter(
        owner=request.user.identity, target=c
    ).exists():
        return Status(404, {"message": "Collection not found"})
    if not c.is_visible_to(request.user):
        return Status(403, {"message": "Permission denied"})
    items = c.item_ids
    stats = {"total": len(items)}
    for st in ShelfType:
        stats[st.value] = 0

    shelf_counts = (
        ShelfMember.objects.filter(owner=request.user.identity, item_id__in=items)
        .values("parent__shelf_type")
        .annotate(count=Count("id"))
    )
    for row in shelf_counts:
        stats[row["parent__shelf_type"]] = row["count"]
    return stats


@api.get(
    "/trending/collection/",
    response={200: list[CollectionSchema]},
    summary="Trending collections",
    auth=None,
    tags=["trending"],
)
@decorate_view(cache_page(600))
def trending_collection(request):
    rot = timezone.now().minute // 6
    collection_ids = cache.get("featured_collections", [])
    i = rot * len(collection_ids) // 10
    collection_ids = collection_ids[i:] + collection_ids[:i]
    from takahe.models import Identity as TakaheIdentity

    restricted_owner_ids = list(
        TakaheIdentity.objects.filter(restriction__gt=0).values_list("pk", flat=True)
    )
    from journal.models.common import prefetch_latest_posts

    # re-check visibility with anonymous-viewer semantics: the endpoint is
    # public and cached, and the id list may include collections whose owners
    # made them non-public after the discover job cached them
    qs = Collection.objects.filter(
        pk__in=collection_ids, visibility=0, owner__anonymous_viewable=True
    ).select_related("owner")
    if restricted_owner_ids:
        qs = qs.exclude(owner_id__in=restricted_owner_ids)
    # pk__in does not preserve list order; reapply the rotation
    by_pk = {c.pk: c for c in qs}
    collections = [by_pk[pk] for pk in collection_ids if pk in by_pk]
    prefetch_latest_posts(collections)
    Collection.attach_item_count_by_category(collections)
    _prefetch_collection_owners(collections)
    return collections
