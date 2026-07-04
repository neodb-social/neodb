from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, F, Q, Window, prefetch_related_objects
from django.db.models.functions import RowNumber
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_page
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods

from common.models import SiteConfig
from common.utils import (
    CustomPaginator,
    PageLinksGenerator,
    get_uuid_or_404,
    user_identity_required,
)
from journal.models import (
    Collection,
    Comment,
    Mark,
    Note,
    Rating,
    Review,
    ShelfManager,
    ShelfMember,
    TagManager,
    q_piece_in_home_feed_of_user,
    q_piece_visible_to_user,
)
from takahe.utils import Takahe

from ..models import (
    ExternalResource,
    IdType,
    Item,
    Podcast,
    TVEpisode,
)
from ..models.people import ItemPeopleRelation, People, PeopleRole
from ..recommendation import blended_for_discover, can_show_reco, similar_items
from ..sites import WikiData

NUM_COMMENTS_ON_ITEM_PAGE = 10


def retrieve_by_uuid(request, item_uid):
    item = get_object_or_404(Item, uid=item_uid)
    url = item.url
    if not url_has_allowed_host_and_scheme(
        url,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        raise Http404()
    return redirect(url)


def retrieve_redirect(request, item_path, item_uuid):
    return redirect(f"/{item_path}/{item_uuid}", permanent=True)


@require_http_methods(["GET", "HEAD"])
@xframe_options_exempt
def embed(request, item_path, item_uuid):
    item = Item.get_by_url(item_uuid)
    if item is None:
        raise Http404(_("Item not found"))
    if item.merged_to_item:
        return redirect(item.merged_to_item.url)
    if item.is_deleted:
        raise Http404(_("Item no longer exists"))
    focus_item = None
    if request.GET.get("focus"):
        focus_item = get_object_or_404(
            Item, uid=get_uuid_or_404(request.GET.get("focus"))
        )
    if request.method == "HEAD":
        return HttpResponse()
    return render(
        request,
        "embed_" + item.class_name + ".html",
        {"item": item, "focus_item": focus_item},
    )


@require_http_methods(["GET", "HEAD"])
@user_identity_required
def retrieve(request, item_path, item_uuid):
    # item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    item = Item.get_by_url(item_uuid)
    if item is None:
        raise Http404(_("Item not found"))
    item_url = f"/{item_path}/{item_uuid}"
    if item.url != item_url:
        return redirect(item.url)
    skipcheck = request.GET.get("skipcheck", False) and request.user.is_authenticated
    if not skipcheck and item.merged_to_item:
        return redirect(item.merged_to_item.url)
    if not skipcheck and item.is_deleted:
        raise Http404(_("Item no longer exists"))
    if request.headers.get("Accept", "").endswith("json"):
        return JsonResponse(item.ap_object, content_type="application/activity+json")
    if request.method == "HEAD":
        return HttpResponse()
    # Prefetch parent item, external resources, and credits to avoid N+1 in templates.
    # The detail page only reads url/site_name/site_label (derived from
    # id_type/id_value) via item.display_resources, so skip the large
    # metadata/other_lookup_ids JSON columns that made this prefetch a slow
    # query (EGGPLANT-1DX). Albums are the exception: album.html renders an
    # embed via Album.get_embed_link(), which reads res.metadata for Bandcamp
    # resources, so keep metadata for them to avoid a per-resource deferred load.
    prefetch_related_objects(
        [item],
        Item.external_resources_prefetch(with_metadata=item.class_name == "album"),
        Item.credits_prefetch(),
    )
    Item.prefetch_parent_items([item])
    # Public tags are shown on the item detail page; aggregate for this single
    # item only (list pages no longer attach tags -- NEODB-SOCIAL-7KW).
    item.tags = TagManager.indexable_tags_for_item(item)
    focus_item = None
    if request.GET.get("focus"):
        focus_item = get_object_or_404(
            Item, uid=get_uuid_or_404(request.GET.get("focus"))
        )
    mark = None
    review = None
    my_collections = []
    collection_list = []
    child_item_comments = []
    shelf_actions = ShelfManager.get_actions_for_category(item.category)
    shelf_statuses = ShelfManager.get_statuses_for_category(item.category)
    if request.user.is_authenticated:
        visible = q_piece_visible_to_user(request.user)
        mark = Mark(request.user.identity, item)
        child_item_comments = Comment.objects.filter(
            owner=request.user.identity, item__in=item.child_items.all()
        )
        review = mark.review
        my_collections = item.collections.all().filter(owner=request.user.identity)
        collection_list = (
            item.collections.all()
            .exclude(owner=request.user.identity)
            .filter(visible)
            .annotate(like_counts=Count("likes"))
            .order_by("-like_counts")
        )
    else:
        collection_list = (
            item.collections.all()
            .filter(visibility=0)
            .annotate(like_counts=Count("likes"))
            .order_by("-like_counts")
        )
    return render(
        request,
        item.class_name + ".html",
        {
            "item": item,
            "item_editable": item.is_editable_by(request.user),
            "focus_item": focus_item,
            "mark": mark,
            "review": review,
            "child_item_comments": child_item_comments,
            "my_collections": my_collections,
            "collection_list": collection_list,
            "shelf_actions": shelf_actions,
            "shelf_statuses": shelf_statuses,
        },
    )


@require_http_methods(["GET"])
def people_works(request, item_path, item_uuid, role):
    item = get_object_or_404(People, uid=get_uuid_or_404(item_uuid))
    if role not in PeopleRole.values:
        raise Http404(_("Invalid role"))
    final = item.final_item
    if final.is_deleted:
        raise Http404(_("Item no longer exists"))
    if final is not item:
        return redirect(f"{final.url}/works/{role}")
    role_label = PeopleRole(role).label

    # All roles this person has, for the role filter dropdown
    all_roles = (
        ItemPeopleRelation.objects.filter(people=item)
        .values_list("role", flat=True)
        .distinct()
    )
    role_choices = [(r, PeopleRole(r).label) for r in all_roles]

    # Filter by role
    qs = ItemPeopleRelation.objects.filter(people=item, role=role)
    item_ids = list(qs.values_list("item_id", flat=True))
    works_qs = Item.objects.filter(
        pk__in=item_ids, is_deleted=False, merged_to_item__isnull=True
    )

    # Filter by shelf status if user is authenticated
    status_filter = request.GET.get("status", "")
    if status_filter and request.user.is_authenticated:
        shelf_item_ids = ShelfMember.objects.filter(
            owner=request.user.identity,
            item_id__in=item_ids,
            parent__shelf_type=status_filter,
        ).values_list("item_id", flat=True)
        works_qs = works_qs.filter(pk__in=shelf_item_ids)

    # Hide child items (e.g. Edition, TVSeason, TVEpisode) when their parent
    # is also visible in this list, to avoid redundant entries. Compute over
    # the already-filtered ids so deleted/merged parents or items outside the
    # status filter do not mask their children.
    visible_ids = list(works_qs.values_list("pk", flat=True))
    hidden_ids = Item.descendant_ids_with_ancestor_in(visible_ids)
    if hidden_ids:
        works_qs = works_qs.exclude(pk__in=hidden_ids)

    # hidden_ids is a subset of visible_ids by construction, so derive the
    # total in-memory instead of issuing another COUNT query.
    total = len(visible_ids) - len(hidden_ids)
    # Order explicitly so pagination yields stable, consistent results.
    works_qs = works_qs.order_by("-pk")
    paginator = CustomPaginator(works_qs, request)
    page_number = request.GET.get("page", default=1)
    works_page = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    # Batch-prefetch per-item data used by _item_card_* partials to avoid N+1
    works_items = list(works_page.object_list)
    if works_items:
        # Card partials only read url/site_name/site_label (derived from
        # id_type/id_value), so skip the large metadata/other_lookup_ids JSON
        # columns that made this prefetch a slow query (EGGPLANT-1DX).
        prefetch_related_objects(
            works_items,
            Item.external_resources_prefetch(),
            Item.credits_prefetch(),
        )
        Item.prefetch_parent_items(works_items)
        Rating.attach_to_items(works_items)
    return render(
        request,
        "people_works.html",
        {
            "item": item,
            "role_label": role_label,
            "current_role": role,
            "role_choices": role_choices,
            "current_status": status_filter,
            "works": works_page,
            "pagination": pagination,
            "total": total,
        },
    )


def episode_data(request, item_uuid):
    item = get_object_or_404(Podcast, uid=get_uuid_or_404(item_uuid))
    qs = item.episodes.all().order_by("-pub_date")
    if request.GET.get("last"):
        qs = qs.filter(pub_date__lt=request.GET.get("last"))
    return render(
        request, "podcast_episode_data.html", {"item": item, "episodes": qs[:5]}
    )


@login_required
def mark_list(request, item_path, item_uuid, following_only=False):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    queryset = ShelfMember.objects.filter(item=item).order_by("-created_time")
    if following_only:
        queryset = queryset.filter(q_piece_in_home_feed_of_user(request.user))
    else:
        queryset = queryset.filter(q_piece_visible_to_user(request.user))
    queryset = queryset.select_related("owner", "parent")
    paginator = CustomPaginator(queryset, request)
    page_number = request.GET.get("page", default=1)
    marks = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    marks_list = list(marks)
    for m in marks_list:
        m.item = item
    _prefetch_mark_list(marks_list, request.user)
    return render(
        request,
        "item_mark_list.html",
        {
            "marks": marks,
            "item": item,
            "followeing_only": following_only,
            "pagination": pagination,
        },
    )


def review_list(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    queryset = Review.objects.filter(item=item).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    paginator = CustomPaginator(queryset, request)
    page_number = request.GET.get("page", default=1)
    reviews = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    return render(
        request,
        "item_review_list.html",
        {
            "reviews": reviews,
            "item": item,
            "pagination": pagination,
        },
    )


def _prefetch_mark_list(members: list["ShelfMember"], user) -> None:
    """Batch-prefetch latest_post, comments, and interaction flags for mark_list."""
    if not members:
        return
    from journal.models import Comment, Mark, Rating
    from journal.models.common import prefetch_latest_posts

    # Prefetch latest_post for ShelfMembers
    prefetch_latest_posts(members)
    # Batch-fetch Comments for all (owner, item) pairs
    pairs = [(m.owner_id, m.item_id) for m in members]

    q = Q()
    for owner_id, item_id in pairs:
        q |= Q(owner_id=owner_id, item_id=item_id)
    comments_by_key: dict[tuple[int, int], Comment] = {}
    ratings_by_key: dict[tuple[int, int], Rating | None] = {}
    owner_map = {m.owner_id: m.owner for m in members}
    item_map = {m.item_id: m.item for m in members}
    if q:
        for c in Comment.objects.filter(q):
            c.owner = owner_map.get(c.owner_id)
            c.item = item_map.get(c.item_id)
            comments_by_key[(c.owner_id, c.item_id)] = c
        for r in Rating.objects.filter(q):
            r.owner = owner_map.get(r.owner_id)
            r.item = item_map.get(r.item_id)
            ratings_by_key[(r.owner_id, r.item_id)] = r
    # Prefetch latest_post for Comments
    comment_list = list(comments_by_key.values())
    if comment_list:
        prefetch_latest_posts(comment_list)
    # Build pre-populated Mark objects on each ShelfMember
    for m in members:
        key = (m.owner_id, m.item_id)
        mark = Mark(m.owner, m.item)
        mark.shelfmember = m
        mark.__dict__["comment"] = comments_by_key.get(key)
        mark.__dict__["rating"] = ratings_by_key.get(key)
        m.__dict__["mark"] = mark
    # Prefetch interaction flags for all posts
    if user.is_authenticated:
        posts = []
        for m in members:
            mark = m.__dict__["mark"]
            if mark.shelfmember and mark.shelfmember.__dict__.get("latest_post"):
                posts.append(mark.shelfmember.__dict__["latest_post"])
            comment = mark.__dict__.get("comment")
            if comment and comment.__dict__.get("latest_post"):
                posts.append(comment.__dict__["latest_post"])
        if posts:
            Takahe.prefetch_interaction_flags(posts, user.identity.pk)


def _prefetch_comments(comments_list: list["Comment"]):
    """Batch-fetch marks, ratings, and latest posts for a list of comments to avoid N+1."""
    if not comments_list:
        return
    from journal.models import Rating
    from journal.models.common import PiecePost

    # Batch-fetch ShelfMembers for all (owner, item) pairs.
    # select_related("parent") eagerly loads the owning Shelf, so mark.action_label
    # (which reads shelfmember.parent.shelf_type) does not trigger a Piece->Shelf
    # polymorphic downcast per comment.
    pairs = {(c.owner_id, c.item_id) for c in comments_list}
    shelfmembers: dict[tuple[int, int], ShelfMember] = {}
    ratings: dict[tuple[int, int], int | None] = {}
    if pairs:
        from django.db.models import Q

        q = Q()
        for owner_id, item_id in pairs:
            q |= Q(owner_id=owner_id, item_id=item_id)
        for sm in ShelfMember.objects.filter(q).select_related("parent"):
            shelfmembers[(sm.owner_id, sm.item_id)] = sm
        for r in Rating.objects.filter(q):
            ratings[(r.owner_id, r.item_id)] = r.grade

    # Batch-fetch Takahe identities so comment.owner.display_name (which reads
    # APIdentity.takahe_identity.name) does not trigger a per-comment lookup.
    Takahe.prefetch_takahe_identities([c.owner for c in comments_list if c.owner_id])

    # Batch-fetch latest post IDs for all comments
    piece_ids = [c.pk for c in comments_list]
    piece_to_post_ids: dict[int, list[int]] = {}
    for piece_id, post_id in PiecePost.objects.filter(
        piece_id__in=piece_ids
    ).values_list("piece_id", "post_id"):
        piece_to_post_ids.setdefault(piece_id, []).append(post_id)
    piece_to_latest: dict[int, int] = {
        pid: max(pids) for pid, pids in piece_to_post_ids.items()
    }
    # Batch-fetch Post objects with authors
    all_post_ids = list(piece_to_latest.values())
    posts_by_id = (
        {p.pk: p for p in Takahe.get_posts(all_post_ids)} if all_post_ids else {}
    )

    # Pre-set mark, rating_grade, and latest_post on each comment
    for c in comments_list:
        key = (c.owner_id, c.item_id)
        m = Mark(c.owner, c.item)
        m.comment = c
        m.shelfmember = shelfmembers.get(key)
        c.__dict__["mark"] = m
        c.__dict__["rating_grade"] = ratings.get(key)
        post_id = piece_to_latest.get(c.pk)
        c.__dict__["latest_post_id"] = post_id
        c.__dict__["latest_post"] = posts_by_id.get(post_id) if post_id else None


def _prefetch_reviews(reviews_list: list["Review"]):
    """Batch-fetch ratings and latest posts for a list of reviews to avoid N+1."""
    if not reviews_list:
        return
    from journal.models.common import prefetch_latest_posts

    # Batch-fetch reviewer's own rating on the reviewed item
    pairs = {(r.owner_id, r.item_id) for r in reviews_list}
    ratings: dict[tuple[int, int], int | None] = {}
    if pairs:
        from django.db.models import Q

        q = Q()
        for owner_id, item_id in pairs:
            q |= Q(owner_id=owner_id, item_id=item_id)
        for rg in Rating.objects.filter(q):
            ratings[(rg.owner_id, rg.item_id)] = rg.grade

    prefetch_latest_posts(reviews_list)
    Takahe.prefetch_takahe_identities([r.owner for r in reviews_list if r.owner_id])
    for r in reviews_list:
        r.__dict__["rating_grade"] = ratings.get((r.owner_id, r.item_id))


def comments(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.class_name == "tvseason":
        ids = [item.pk]
    else:
        ids = item.child_item_ids + [item.pk] + item.sibling_item_ids
    queryset = (
        Comment.objects.filter(item_id__in=ids)
        .order_by("-created_time")
        .select_related("owner")
        .prefetch_related("item")
    )
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    comments_list = list(queryset[: NUM_COMMENTS_ON_ITEM_PAGE + 1])
    _prefetch_comments(comments_list)
    return render(
        request,
        "_item_comments.html",
        {
            "item": item,
            "comments": comments_list,
        },
    )


def comments_by_episode(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    episode_uuid = request.GET.get("episode_uuid")
    if episode_uuid:
        episode = TVEpisode.get_by_url(episode_uuid)
        ids = [episode.pk] if episode else []
    else:
        ids = item.child_item_ids
    queryset = Comment.objects.filter(item_id__in=ids).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    return render(
        request,
        "_item_comments_by_episode.html",
        {
            "item": item,
            "episode_uuid": episode_uuid,
            "comments": queryset[: NUM_COMMENTS_ON_ITEM_PAGE + 1],
        },
    )


def reviews(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    ids = item.child_item_ids + [item.pk] + item.sibling_item_ids
    queryset = (
        Review.objects.filter(item_id__in=ids)
        .order_by("-created_time")
        .select_related("owner", "owner__user")
        .prefetch_related("item")
    )
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    reviews_list = list(queryset[: NUM_COMMENTS_ON_ITEM_PAGE + 1])
    _prefetch_reviews(reviews_list)
    return render(
        request,
        "_item_reviews.html",
        {
            "item": item,
            "reviews": reviews_list,
        },
    )


def similar(request, item_path, item_uuid):
    """HTMX partial: items similar to the given one.

    Returns an empty fragment if the surface is disabled (site or user pref)
    or there are no similar items. The block is hidden visually whenever the
    fragment is empty.
    """
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    items: list = []
    if can_show_reco(request.user, "similar_items"):
        items = similar_items(item, request.user, limit=10)
    if items:
        Item.prefetch_parent_items(items)
        Item.prefetch_edition_works(items)
        # Card partials only read url/site_name/site_label (derived from
        # id_type/id_value), so skip the large metadata/other_lookup_ids JSON
        # columns that made this prefetch a slow query (EGGPLANT-1DX).
        prefetch_related_objects(items, Item.external_resources_prefetch())
    return render(
        request,
        "_item_similar.html",
        {"item": item, "items": items},
    )


def notes(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    ids = item.child_item_ids + [item.pk] + item.sibling_item_ids
    queryset = Note.objects.filter(item_id__in=ids).order_by("-created_time")
    queryset = queryset.filter(q_piece_visible_to_user(request.user))
    from_note = request.GET.get("from", "")
    if from_note:
        note = get_object_or_404(Note, uid=get_uuid_or_404(from_note))
        queryset = queryset.filter(owner=note.owner)
        queryset = queryset.exclude(pk=note.pk)
    else:
        queryset = queryset.annotate(
            row_number=Window(
                expression=RowNumber(),
                partition_by=[F("owner_id")],
                order_by=F("created_time").desc(),
            ),
            rows=Window(
                expression=Count("owner_id"),
                partition_by=[F("owner_id")],
            ),
        ).filter(row_number=1)
    before_time = request.GET.get("last")
    if before_time:
        queryset = queryset.filter(created_time__lte=before_time)
    return render(
        request,
        "_item_notes.html",
        {
            "item": item,
            "from_note": from_note,
            "notes": queryset[: NUM_COMMENTS_ON_ITEM_PAGE + 1],
        },
    )


@cache_page(3600 * 24)
@require_http_methods(["GET", "HEAD"])
def wikipedia_pages(request, item_path, item_uuid, wikidata_id):
    """HTMX endpoint to display Wikipedia pages for a WikiData entity"""
    wikidata = get_object_or_404(
        ExternalResource, id_value=wikidata_id, id_type=IdType.WikiData
    )
    site = WikiData(id_value=wikidata.id_value)
    wiki_pages = sorted(
        site.get_wikipedia_pages(),
        key=lambda p: (
            p["lang"].split("-")[0] not in SiteConfig.system.preferred_languages
        ),
    )
    return render(
        request,
        "_wikipedia_pages.html",
        {
            "item": wikidata.item,
            "wikidata_url": wikidata.url,
            "wikipedia_pages": wiki_pages,
        },
    )


def discover(request):
    cache_key = "public_gallery"
    gallery_list = cache.get(cache_key, [])

    if not SiteConfig.system.discover_show_verified_podcasts:
        gallery_list = [g for g in gallery_list if g["name"] != "original_episodes"]

    # rotate every 6 minutes
    rot = timezone.now().minute // 6
    for gallery in gallery_list:
        items = cache.get(gallery["name"], [])
        i = rot * len(items) // 10
        gallery["items"] = items[i:] + items[:i]

    if request.user.is_authenticated:
        layout = request.user.preference.discover_layout
        identity = request.user.identity
        announcements = []
    else:
        identity = None
        layout = []
        announcements = Takahe.get_announcements()

    collection_ids = cache.get("featured_collections", [])
    if collection_ids:
        i = rot * len(collection_ids) // 10
        collection_ids = collection_ids[i:] + collection_ids[:i]
        featured_collections = Collection.objects.filter(pk__in=collection_ids)
    else:
        featured_collections = []

    if SiteConfig.system.discover_show_popular_tags:
        popular_tags = cache.get("popular_tags", [])
    else:
        popular_tags = None

    reco_items = []
    if request.user.is_authenticated:
        reco_items = blended_for_discover(request.user, limit=30)
        if len(reco_items) < 3:
            reco_items = []
        else:
            Item.prefetch_parent_items(reco_items)
            Item.prefetch_edition_works(reco_items)
            # Discover cards skip the metadata JSON (EGGPLANT-1DX).
            prefetch_related_objects(reco_items, Item.external_resources_prefetch())
            cat_order = {
                cat: i
                for i, cat in enumerate(
                    dict.fromkeys(str(it.category) for it in reco_items)
                )
            }
            reco_items.sort(key=lambda i: cat_order[str(i.category)])

    updated = cache.get("trends_updated", timezone.now())
    return render(
        request,
        "discover.html",
        {
            "identity": identity,
            "all_announcements": announcements,
            "gallery_list": gallery_list,
            "featured_collections": featured_collections,
            "popular_tags": popular_tags,
            "layout": layout,
            "updated": updated,
            "reco_items": reco_items,
        },
    )


def discover_original_podcasts(request):
    """Paginated list of podcasts that have a verified creator.

    These are the shows behind the discover page's "original episodes"
    shelf; clicking the shelf heading lands here.
    """
    queryset = Podcast.verified_originals()
    paginator = CustomPaginator(queryset, request)
    page_number = request.GET.get("page", default=1)
    podcasts = paginator.get_page(page_number)
    pagination = PageLinksGenerator(page_number, paginator.num_pages, request.GET)
    podcast_items = list(podcasts.object_list)
    if podcast_items:
        prefetch_related_objects(
            podcast_items,
            Item.external_resources_prefetch(),
            Item.credits_prefetch(),
        )
        Rating.attach_to_items(podcast_items)
        if request.user.is_authenticated:
            Mark.attach_to_items(request.user.identity, podcast_items, request.user)
    return render(
        request,
        "discover_original_podcasts.html",
        {
            "podcasts": podcasts,
            "pagination": pagination,
            "total": paginator.count,
        },
    )


@login_required
@require_http_methods(["GET"])
def discover_popular_posts(request):
    if SiteConfig.system.discover_show_popular_posts:
        post_ids = cache.get("popular_posts", [])
        popular_posts = Takahe.get_posts(post_ids).order_by("-published")
    else:
        popular_posts = Takahe.get_public_posts(
            SiteConfig.system.discover_show_local_only
        )
    popular_posts = (
        popular_posts.not_blocked_by(request.user.identity.takahe_identity)
        .annotate(
            author_row=Window(
                expression=RowNumber(),
                partition_by="author_id",
                order_by="-published",
            )
        )
        .filter(author_row__lte=2)
    )
    posts = list(popular_posts[:20])
    Takahe.prefetch_interaction_flags(posts, request.user.identity.pk)
    return render(
        request,
        "_discover_popular_posts.html",
        {"popular_posts": posts},
    )
