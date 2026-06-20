from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.core.signing import b62_encode
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from catalog.models import Item
from common.models import int_
from common.sentry import record_activity
from common.utils import (
    AuthedHttpRequest,
    PageLinksGenerator,
    get_page_size_from_request,
    get_uuid_or_404,
)
from takahe.auth import _SigError, verify_http_signature
from users.models import User

from ..forms import *
from ..models import *
from ..models.itemlist import list_add
from ..models.renderers import sanitize_md_images
from .common import (
    conditional_get_for_anonymous,
    post_quotes_count,
    render_relogin,
    target_identity_required,
)


@login_required
def add_to_collection(request: AuthedHttpRequest, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if request.method == "GET":
        collections = Collection.objects.filter(
            owner=request.user.identity, query__isnull=True
        )
        return render(
            request,
            "add_to_collection.html",
            {
                "smart": False,
                "item": item,
                "collections": collections,
            },
        )
    else:
        cid = int_(request.POST.get("collection_id"))
        if not cid:
            cid = Collection.objects.create(
                owner=request.user.identity,
                title=_("Collection by {0}").format(request.user.display_name),
            ).pk
        collection = Collection.objects.get(owner=request.user.identity, id=cid)
        collection.append_item(item, note=request.POST.get("note"))
        referer = request.META.get("HTTP_REFERER") or ""
        if not url_has_allowed_host_and_scheme(
            referer,
            allowed_hosts=set(settings.SITE_DOMAINS),
            require_https=settings.SSL_ONLY,
        ):
            referer = "/"
        return HttpResponseRedirect(referer)


@login_required
def save_as_dynamic_collection(request: AuthedHttpRequest):
    query = request.GET.get("q", "").strip()
    if not query:
        raise BadRequest(_("Invalid parameter"))
    if request.method == "GET":
        collections = Collection.objects.filter(
            owner=request.user.identity, query__isnull=False
        )
        return render(
            request,
            "add_to_collection.html",
            {
                "smart": True,
                "query": query,
                "collections": collections,
            },
        )
    else:
        cid = int_(request.POST.get("collection_id"))
        if not cid:
            collection = Collection.objects.create(
                owner=request.user.identity,
                title=_("Collection by {0}").format(request.user.display_name),
                query=query,
            )
        else:
            collection = Collection.objects.get(owner=request.user.identity, id=cid)
            if collection.query is None:
                raise BadRequest(_("Invalid parameter"))
            collection.query = query
            collection.save()
        return HttpResponseRedirect(collection.url)


def collection_retrieve_redirect(request: AuthedHttpRequest, collection_uuid):
    uid = get_uuid_or_404(collection_uuid)
    # `uid` is a uuid.UUID; NeoDB URLs use the Base62-encoded form, so
    # re-encode rather than rely on `str(uid)` (which is hyphenated).
    return redirect(f"/collection/{b62_encode(uid.int).zfill(22)}", permanent=True)


def _resolve_signed_viewer(request):
    """Return ``(viewer | None, error_response | None)``.

    Signatures are verified when present and resolve to the signing
    ``APIdentity``; absent signatures resolve to ``None`` (anonymous);
    invalid signatures yield a 401 response.
    """
    if not request.headers.get("Signature"):
        return None, None
    try:
        return verify_http_signature(request), None
    except _SigError as e:
        return None, HttpResponse(
            f"Bad signature: {e}", status=401, content_type="text/plain"
        )


def _is_locally_owned(instance) -> bool:
    """True iff the list and its owner are both local. ``Piece.local`` is
    False on mirrors but auto-initialized rows (e.g. ``ShelfManager``
    creates a Shelf row per ``ShelfType`` for any APIdentity, including
    remote ones, with ``local=True`` by default), so we also check that
    the owner is a local APIdentity. A remote owner is the authoritative
    one for their own content; we must not pretend to speak for them.
    """
    if not getattr(instance, "local", False):
        return False
    owner = getattr(instance, "owner", None)
    return bool(owner and getattr(owner, "local", False))


def _list_ap_object_view(request, instance):
    """Dereferenceable AP endpoint for any List subclass (Collection, Shelf).

    Returns the lightweight Shelf envelope; the items list lives behind
    ``first``/``last`` URLs that point at ``_list_items_view``.
    """
    # Mirror / remote-owned lists must not be served back over AP — the
    # origin server is authoritative; we hold a possibly-stale snapshot
    # and have no business pretending to speak for them. Peers chasing a
    # federated link should follow the original `id` URL on the origin.
    if not _is_locally_owned(instance):
        return JsonResponse({"error": "Not found"}, status=404)
    viewer, err = _resolve_signed_viewer(request)
    if err is not None:
        return err
    if not instance.is_visible_to_identity(viewer):
        # 404 rather than 403 to avoid leaking existence to unauthorized callers.
        return JsonResponse({"error": "Not found"}, status=404)
    return JsonResponse(
        instance.ap_envelope(),
        content_type="application/activity+json",
    )


def _list_items_view(request, instance):
    """Paginated AP items endpoint for any List subclass.

    No ``page`` query param: returns the ``OrderedCollection`` envelope.
    ``?page=N``: returns one ``OrderedCollectionPage`` slice.
    """
    # Same gate as ``_list_ap_object_view`` — never serve the items list
    # of a mirror or a remote-owned auto-initialized shelf row; the
    # origin owns it.
    if not _is_locally_owned(instance):
        return JsonResponse({"error": "Not found"}, status=404)
    viewer, err = _resolve_signed_viewer(request)
    if err is not None:
        return err
    if not instance.is_visible_to_identity(viewer):
        return JsonResponse({"error": "Not found"}, status=404)
    raw_page = request.GET.get("page")
    if raw_page is None:
        body = instance.ap_items_envelope()
    else:
        try:
            page = int(raw_page)
        except TypeError, ValueError:
            return HttpResponse("Bad page", status=400, content_type="text/plain")
        body = instance.ap_items_page(page)
    return JsonResponse(body, content_type="application/activity+json")


def collection_ap_items(request, collection_uuid):
    """URL handler for ``/collection/<uuid>/items`` (AP only).

    Always returns AP, regardless of Accept header — this URL is the
    items endpoint advertised by the Shelf envelope's ``first``/``last``
    fields, and HTML browsing for the same data lives at the parent
    Collection page.
    """
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    return _list_items_view(request, collection)


def _collection_last_modified(request, collection_uuid):
    try:
        uid = get_uuid_or_404(collection_uuid)
    except Http404:
        return None
    collection = Collection.objects.filter(uid=uid).select_related("owner").first()
    if not collection:
        return None
    # Dynamic collections render from a fresh query each time; their
    # member set can drift without ``edited_time`` moving, so a 304 is
    # not safe.
    if collection.is_dynamic:
        return None
    # Owner-level privacy (``anonymous_viewable``, ``restricted``) is not
    # reflected in ``edited_time``; check visibility before 304 so a
    # flip doesn't leave anonymous clients with a cached 200.
    if not collection.is_visible_to(request.user):
        return None
    return collection.edited_time


@conditional_get_for_anonymous(_collection_last_modified)
def collection_retrieve(request: AuthedHttpRequest, collection_uuid):
    # AP clients consume the Shelf envelope inline from the announcement
    # Post's ``relatedWith[0]`` (which carries the items endpoint URL in
    # its ``first``/``last`` fields); the canonical ``/collection/<uuid>/``
    # only needs to render HTML for humans. Mirrors ``article_retrieve``
    # / ``review_retrieve``.
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    page_number = int_(request.GET.get("page"), 1)
    per_page = get_page_size_from_request(request)
    viewer = request.user.identity if request.user.is_authenticated else None
    comment_as_note = viewer != collection.owner
    members, pages = collection.get_members_by_page(
        page_number, per_page, viewer, comment_as_note
    )
    pagination = PageLinksGenerator(page_number, pages, request.GET)
    follower_count = collection.likes.all().count()
    following = (
        Like.user_liked_piece(request.user.identity, collection)
        if request.user.is_authenticated
        else False
    )
    featured_since = (
        collection.featured_since(request.user.identity)
        if request.user.is_authenticated
        else None
    )
    available_as_featured = (
        request.user.is_authenticated
        and (following or request.user.identity == collection.owner)
        and not featured_since
        and collection.trackable
    )
    stats = {}
    if featured_since and collection.trackable:
        stats = collection.get_stats(request.user.identity)
        stats["wishlist_deg"] = (
            round(stats["wishlist"] / stats["total"] * 360) if stats["total"] else 0
        )
        stats["progress_deg"] = (
            round(stats["progress"] / stats["total"] * 360) if stats["total"] else 0
        )
        stats["complete_deg"] = (
            round(stats["complete"] / stats["total"] * 360) if stats["total"] else 0
        )
    return render(
        request,
        "collection.html",
        {
            "collection": collection,
            "members": members,
            "pagination": pagination,
            "follower_count": follower_count,
            "following": following,
            "stats": stats,
            "available_as_featured": available_as_featured,
            "featured_since": featured_since,
            "editable": collection.is_editable_by(request.user),
            "quotes_count": post_quotes_count(collection.latest_post),
        },
    )


@login_required
@require_http_methods(["POST"])
def collection_add_featured(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    FeaturedCollection.objects.update_or_create(
        owner=request.user.identity, target=collection
    )
    referer = request.META.get("HTTP_REFERER") or ""
    if not url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        referer = "/"
    return HttpResponseRedirect(referer)


@login_required
@require_http_methods(["POST"])
def collection_remove_featured(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    fc = FeaturedCollection.objects.filter(
        owner=request.user.identity, target=collection
    ).first()
    if fc:
        fc.delete()
    referer = request.META.get("HTTP_REFERER") or ""
    if not url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        referer = "/"
    return HttpResponseRedirect(referer)


@login_required
@require_http_methods(["POST", "GET"])
def collection_share(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(
        Collection, uid=get_uuid_or_404(collection_uuid) if collection_uuid else None
    )
    user = request.user
    if collection and not collection.is_visible_to(user):
        raise PermissionDenied(_("Insufficient permission"))
    if request.method == "GET":
        return render(request, "collection_share.html", {"collection": collection})
    else:
        comment = request.POST.get("comment", "")
        # boost if possible, otherwise quote
        if (
            not comment
            and user.preference.mastodon_repost_mode == 0
            and collection.latest_post
        ):
            if user.mastodon:
                user.mastodon.boost_later(collection.latest_post.url)
        else:
            visibility = VisibilityType(int_(request.POST.get("visibility")))
            link = (
                collection.latest_post.url
                if collection.latest_post
                else collection.absolute_url
            ) or ""
            if not share_collection(collection, comment, user, visibility, link):
                return render_relogin(request)
        referer = request.META.get("HTTP_REFERER") or ""
        if not url_has_allowed_host_and_scheme(
            referer,
            allowed_hosts=set(settings.SITE_DOMAINS),
            require_https=settings.SSL_ONLY,
        ):
            referer = "/"
        return HttpResponseRedirect(referer)


def share_collection(
    collection: Collection,
    comment: str,
    user: User,
    visibility: VisibilityType,
    link: str,
):
    if not user or not user.mastodon:
        return
    tags = (
        "\n"
        + user.preference.mastodon_append_tag.replace("[category]", _("collection"))
        if user.preference.mastodon_append_tag
        else ""
    )
    user_str = (
        _("shared my collection")
        if user == collection.owner.user
        else (
            _("shared {username}'s collection").format(
                username=(
                    " @" + collection.owner.user.mastodon.handle + " "
                    if collection.owner.user.mastodon
                    else " " + collection.owner.username + " "
                )
            )
        )
    )
    content = f"{user_str}:{collection.title}\n{link}\n{comment}{tags}"
    try:
        user.mastodon.post(content, visibility)
        return True
    except Exception:
        return False


@login_required
@require_http_methods(["GET"])
def collection_edit_items(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if collection.is_dynamic:
        members = []
    else:
        members_qs = collection.ordered_members
        last_pos = int_(request.GET.get("last_pos"))
        if last_pos:
            last_member = int_(request.GET.get("last_member"))
            members_qs = members_qs.filter(position__gte=last_pos).exclude(
                id=last_member
            )
        members = list(members_qs[:20])
        # Member cards skip the metadata JSON (EGGPLANT-1DX).
        item_ids = [m.item_id for m in members]
        items = list(
            Item.objects.filter(pk__in=item_ids).prefetch_related(
                Item.external_resources_prefetch()
            )
        )
        items_map = {i.pk: i for i in items}
        for member in members:
            member.item = items_map.get(member.item_id)
    return render(
        request,
        "collection_items.html",
        {
            "collection": collection,
            "members": members,
            "collection_edit": True,
        },
    )


@login_required
@require_http_methods(["POST"])
def collection_append_item(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if collection.is_dynamic:
        raise BadRequest(_("Dynamic collection is not editable"))
    url = request.POST.get("url", "")
    note = request.POST.get("note", "")
    item = Item.get_by_url(url)
    member = None
    if item:
        member, new = collection.append_item(item, note=note)
        # ``append_item`` fires the ``list_add`` signal, which the
        # ``_collection_member_changed`` receiver maps to
        # ``collection.save()`` (and federation re-post). No explicit save
        # needed here.
        if new:
            msg = None
        else:
            member = None
            msg = _("The item is already in the collection.")
    else:
        msg = _("Unable to find the item, please use item url from this site.")
    return render(
        request,
        "collection_items.html",
        {
            "collection": collection,
            "members": [member] if member else [],
            "collection_edit": True,
            "msg": msg,
        },
    )


@login_required
@require_http_methods(["POST"])
def collection_remove_item(request: AuthedHttpRequest, collection_uuid, item_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if collection.is_dynamic:
        raise BadRequest(_("Dynamic collection is not editable"))
    collection.remove_item(item)
    return HttpResponse("")


@login_required
@require_http_methods(["POST"])
def collection_update_member_order(request: AuthedHttpRequest, collection_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if collection.is_dynamic:
        raise BadRequest(_("Dynamic collection is not editable"))
    ids = request.POST.get("member_ids", "").strip()
    if not ids:
        raise BadRequest(_("Invalid parameter"))
    ordered_member_ids = [int_(i) for i in ids.split(",")]
    collection.update_member_order(ordered_member_ids)
    return render(
        request,
        "collection_items.html",
        {
            "collection": collection,
            "members": [],
            "collection_edit": True,
            "msg": _("Saved."),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def collection_update_item_note(request: AuthedHttpRequest, collection_uuid, item_uuid):
    collection = get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if collection.is_dynamic:
        raise BadRequest(_("Dynamic collection is not editable"))
    member = collection.get_member_for_item(item)
    note = request.POST.get("note", default="")
    cancel = request.GET.get("cancel")
    if request.method == "POST" and member:
        member.note = note
        member.save()
        # Re-emit list_add so the Collection receiver bumps edited_time
        # and federates the updated member-state — the receiver gates on
        # is_dynamic itself.
        list_add.send(sender=Collection, instance=collection, item=item, member=member)
        return render(
            request,
            "collection_update_item_note_ok.html",
            {"collection": collection, "item": item, "collection_member": member},
        )
    elif cancel:
        return render(
            request,
            "collection_update_item_note_ok.html",
            {"collection": collection, "item": item, "collection_member": member},
        )
    else:
        return render(
            request,
            "collection_update_item_note.html",
            {"collection": collection, "item": item, "collection_member": member},
        )


@login_required
@require_http_methods(["GET", "POST"])
def collection_edit(request: AuthedHttpRequest, collection_uuid=None):
    collection = (
        get_object_or_404(Collection, uid=get_uuid_or_404(collection_uuid))
        if collection_uuid
        else None
    )
    if collection and not collection.is_editable_by(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if request.method == "GET":
        form = CollectionForm(instance=collection) if collection else CollectionForm()
        if request.GET.get("title"):
            form.instance.title = request.GET.get("title")
        return render(
            request,
            "collection_edit.html",
            {
                "form": form,
                "collection": collection,
                "user": collection.owner.user if collection else request.user,
                "identity": collection.owner if collection else request.user.identity,
            },
        )
    else:
        form = (
            CollectionForm(request.POST, request.FILES, instance=collection)
            if collection
            else CollectionForm(request.POST)
        )
        if form.is_valid():
            if not collection:
                form.instance.owner = request.user.identity
            form.instance.brief = sanitize_md_images(form.instance.brief)
            form.save()
            record_activity("collection", "web")
            return redirect(
                reverse("journal:collection_retrieve", args=[form.instance.uuid])
            )
        else:
            raise BadRequest(_("Invalid parameter"))


@target_identity_required
def user_collection_list(request: AuthedHttpRequest, user_name):
    from journal.models.common import prefetch_latest_posts
    from takahe.utils import Takahe

    target = request.target_identity
    collections = list(
        Collection.objects.filter(owner=target)
        .filter(q_owned_piece_visible_to_user(request.user, target))
        .order_by("-edited_time")
    )
    prefetch_latest_posts(collections)
    if request.user.is_authenticated:
        posts = [c.latest_post for c in collections if c.latest_post]
        Takahe.prefetch_interaction_flags(posts, request.user.identity.pk)
    return render(
        request,
        "user_collection_list.html",
        {
            "user": target.user,
            "identity": target,
            "collections": collections,
        },
    )


@target_identity_required
def user_liked_collection_list(request: AuthedHttpRequest, user_name):
    from journal.models.common import prefetch_latest_posts
    from takahe.utils import Takahe

    target = request.target_identity
    collections = Collection.objects.filter(
        interactions__identity=target,
        interactions__interaction_type="like",
        interactions__target_type="Collection",
    ).order_by("-edited_time")
    if target.user != request.user:
        collections = collections.filter(q_piece_visible_to_user(request.user))
    collections = list(collections)
    prefetch_latest_posts(collections)
    if request.user.is_authenticated:
        posts = [c.latest_post for c in collections if c.latest_post]
        Takahe.prefetch_interaction_flags(posts, request.user.identity.pk)
    return render(
        request,
        "user_collection_list.html",
        {
            "user": target.user,
            "identity": target,
            "collections": collections,
            "liked": True,
        },
    )
