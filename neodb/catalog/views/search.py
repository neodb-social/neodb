import re
from urllib.parse import urlparse

import django_rq
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.db.models import Prefetch, prefetch_related_objects
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from rq.job import Job

from common.models import SiteConfig, int_
from common.utils import (
    HTTPResponseHXRedirect,
    PageLinksGenerator,
    get_page_size_from_request,
    user_identity_required,
)
from journal.jobs.list_sync import extract_items_url
from journal.models import Collection, Note, Review, Shelf, Tag
from journal.models.mark import Mark
from journal.models.rating import Rating
from users.views import query_identity

from ..common.sites import AbstractSite, SiteManager
from ..models import (
    ExternalResource,
    Item,
    ItemCategory,
    ItemCredit,
    SiteName,
    item_categories,
)
from ..search import (
    ExternalSources,
    PeopleIndex,
    PeopleQueryParser,
    enqueue_fetch,
    get_fetch_lock,
    query_index,
)


def default_visible_categories() -> list[ItemCategory]:
    return [
        x
        for x in item_categories()
        if x.value not in SiteConfig.system.hidden_categories
    ]


def fetch_refresh(request, job_id):
    try:
        job = Job.fetch(id=job_id, connection=django_rq.get_connection("fetch"))
        item_url = job.return_value()
    except Exception:
        item_url = "-"
    if item_url:
        if item_url == "-":
            return render(request, "_fetch_failed.html")
        else:
            return HTTPResponseHXRedirect(item_url)
    else:
        retry = int_(request.GET.get("retry", 0)) + 1
        if retry > 10:
            return render(request, "_fetch_failed.html")
        else:
            return render(
                request,
                "_fetch_refresh.html",
                {"job_id": job_id, "retry": retry, "delay": retry * 2},
            )


def fetch(request, url, site: AbstractSite | None, is_refetch: bool = False):
    item = site.get_item(allow_rematch=False) if site else None
    if item and not is_refetch:
        return redirect(item.url)
    if item and is_refetch:
        item.log_action(
            {
                "!refetch": [url, None],
            }
        )
    job_id = None
    if is_refetch or get_fetch_lock(request.user, url):
        job_id = enqueue_fetch(url, is_refetch, request.user)
    return render(
        request,
        "fetch_pending.html",
        {
            "source": site.SITE_NAME.label if site else _("the internet"),
            "sites": SiteName.labels,
            "job_id": job_id,
        },
    )


def _maybe_remote_piece(url: str, user):
    """Return a locally-mirrored remote Piece (Collection, Review, Note) whose
    AP id matches the pasted URL, if one exists *and* is visible to ``user``.

    Visibility is applied here so a non-authorized viewer cannot use the
    redirect to confirm the existence of a private or followers-only mirror,
    and cannot trigger the resync side effect by paste-probing remote URLs.

    For Collection / Shelf we also enqueue a paginated member resync so
    newly-fetched catalog items show up on a follow-up view. TODO:
    trigger an analogous refetch for Review / Note so out-of-sync
    mirrors can be refreshed by URL paste.
    """
    for cls in (Collection, Shelf, Review, Note):
        piece = cls.objects.filter(remote_id=url, local=False).first()
        if piece and piece.is_visible_to(user):
            if isinstance(piece, (Collection, Shelf)):
                # Recover the items endpoint URL (and any inline
                # ``orderedItems``) from the cached announcement Post's
                # envelope — Collection's AP id is HTML-only post the
                # inline-only refactor, so the job can't re-dereference
                # ``remote_id`` for these fields.
                items_url, inline_items = _list_sync_args_from_post(piece)
                # When the cache yields nothing (announcement Post
                # deleted, or malformed ``type_data``), skip the
                # enqueue rather than scheduling a no-op job: the job
                # has no path to recover the items URL without
                # re-dereferencing ``remote_id``, which is HTML for
                # peers on the new code. The user still receives the
                # existing mirror; a refresh will arrive with the next
                # pushed Update activity from the origin.
                if items_url or inline_items is not None:
                    try:
                        django_rq.get_queue("fetch").enqueue(
                            "journal.jobs.list_sync.fetch_remote_list_members",
                            f"{type(piece).__module__}.{type(piece).__name__}",
                            piece.pk,
                            items_url,
                            inline_items,
                        )
                    except Exception:
                        pass
            return piece
    return None


def _list_sync_args_from_post(piece):
    """Pull ``items_url`` and ``inline_items`` out of the cached
    announcement Post's stored envelope so URL-paste refreshes can
    re-enqueue ``fetch_remote_list_members`` without re-fetching the
    Shelf envelope. Returns ``(None, None)`` if no Post is linked or
    the structure isn't what we wrote on inbound."""
    post = piece.latest_post
    if not post or not isinstance(post.type_data, dict):
        return None, None
    related = (post.type_data.get("object") or {}).get("relatedWith") or []
    if not related or not isinstance(related[0], dict):
        return None, None
    envelope = related[0]
    items_url = extract_items_url(envelope)
    inline_items_raw = envelope.get("orderedItems")
    inline_items = inline_items_raw if isinstance(inline_items_raw, list) else None
    return items_url, inline_items


def resolve_url_query(request, keywords):
    """If `keywords` looks like a URL, resolve it to a redirect or fetch
    response and return it; otherwise return None.

    Shared by the generic and people/org search views so paste-URL
    behavior stays consistent.
    """
    if keywords.find("://") <= 0:
        return None
    host = urlparse(keywords).hostname
    allowed_hosts = set(settings.SITE_DOMAINS)
    # Don't require HTTPS here: pasting `http://oursite/path` should still
    # redirect to the same path; the server / browser handles the scheme
    # upgrade when SSL_ONLY is enforced upstream.
    if (
        host
        and host in allowed_hosts
        and url_has_allowed_host_and_scheme(keywords, allowed_hosts=allowed_hosts)
    ):
        return redirect(keywords)
    remote_piece = _maybe_remote_piece(keywords, request.user)
    if remote_piece:
        return redirect(remote_piece.url)
    # skip detecting redirection to avoid timeout
    site = SiteManager.get_site_by_url(
        keywords, detect_redirection=False, detect_fallback=False
    )
    if site:
        return fetch(request, keywords, site, False)
    if request.GET.get("r") and url_has_allowed_host_and_scheme(
        keywords, allowed_hosts=allowed_hosts, require_https=settings.SSL_ONLY
    ):
        return redirect(keywords)
    return fetch(request, keywords, None, False)


def visible_categories(request):
    if not request or not hasattr(request, "user") or not request.user.is_authenticated:
        return default_visible_categories()
    vc = request.session.get("p_categories", None)
    if vc is None:
        vc = [
            x
            for x in item_categories()
            if x.value not in request.user.preference.hidden_categories
        ]
        request.session["p_categories"] = vc
    return vc


@user_identity_required
def search(request):
    category = request.GET.get("c", default="all").strip().lower()
    keywords = request.GET.get("q", default="").strip()
    if re.match(r"^[@＠]", keywords):
        return query_identity(request, keywords.replace("＠", "@"))
    hide_category = False
    if category == "all" or not category:
        category = None
        categories = visible_categories(request)
    elif category == "movietv":
        categories = [ItemCategory.Movie, ItemCategory.TV]
    else:
        try:
            categories = [ItemCategory(category)]
            hide_category = True
        except Exception:
            categories = visible_categories(request)
    tag = request.GET.get("tag", default="").strip()
    tag = Tag.deep_cleanup_title(tag, default="")
    p = int_(request.GET.get("page", default="1"), 1)
    sites = [n.label for n in SiteName if n != SiteName.Unknown]
    if not (keywords or tag):
        return render(
            request,
            "search_results.html",
            {
                "items": None,
                "sites": sites,
            },
        )

    url_response = resolve_url_query(request, keywords)
    if url_response is not None:
        return url_response

    if tag:
        redir = reverse("common:search") + f'?q=tag:"{tag}"'
        return redirect(redir)
    excl = (
        request.user.preference.hidden_categories
        if request.user.is_authenticated
        else None
    )
    per_page = get_page_size_from_request(request)
    items, num_pages, __, by_cat, q = query_index(
        keywords, categories, p, exclude_categories=excl, per_page=per_page
    )
    # Include duplicates attached as `dupe_to`: the template renders them
    # via a nested {% include '_list_item.html' %} loop, so they need the
    # same prefetches/attachments to avoid N+1 in templates.
    all_items = list(items)
    for i in items:
        all_items.extend(getattr(i, "dupe_to", []) or [])
    Item.prefetch_parent_items(all_items)
    prefetch_related_objects(
        all_items,
        Prefetch("credits", queryset=ItemCredit.objects.select_related("person")),
    )
    Rating.attach_to_items(all_items)
    # Public tags come from the search index (attached in CatalogSearchResult.items),
    # so we skip the per-request journal_tagmember aggregation here (NEODB-SOCIAL-7KW).
    if request.user.is_authenticated:
        Mark.attach_to_items(request.user.identity, all_items, request.user)
    return render(
        request,
        "search_results.html",
        {
            "items": items,
            "pagination": PageLinksGenerator(p, num_pages, request.GET),
            "sites": sites,
            "hide_category": hide_category,
            "by_category": by_cat,
            "q": q,
        },
    )


@user_identity_required
def people_search(request):
    keywords = request.GET.get("q", default="").strip()
    people_type = request.GET.get("type", default="").strip().lower() or None
    p = int_(request.GET.get("page", default="1"), 1)
    per_page = get_page_size_from_request(request)
    sites = [n.label for n in SiteName if n != SiteName.Unknown]
    if not keywords:
        return render(
            request,
            "search_results_people.html",
            {"items": None, "sites": sites},
        )
    url_response = resolve_url_query(request, keywords)
    if url_response is not None:
        return url_response
    parser = PeopleQueryParser(
        keywords, page=p, page_size=per_page, people_type=people_type
    )
    result = PeopleIndex.instance().search(parser) if parser else None
    # Note: SearchResult.__bool__ is "has hits", so `bool(result)` is False on
    # outage even when result.error is set. Compare to None explicitly.
    search_error = result is not None and bool(result.error)
    items = [] if result is None or search_error else result.items
    num_pages = 0 if result is None or search_error else result.pages
    return render(
        request,
        "search_results_people.html",
        {
            "items": items,
            "pagination": PageLinksGenerator(p, num_pages, request.GET),
            "sites": sites,
            "q": keywords,
            "search_error": search_error,
        },
    )


@login_required
def external_search(request):
    category = request.GET.get("c", default="all").strip().lower()
    keywords = request.GET.get("q", default="").strip()
    page_number = int_(request.GET.get("page"), 1)
    disabled = request.user.preference.disabled_search_sources or []
    items = (
        ExternalSources.search(
            keywords,
            page_number,
            category,
            visible_categories(request),
            disabled_sources=disabled,
        )
        if keywords
        else []
    )
    return render(request, "external_search_results.html", {"external_items": items})


@login_required
@require_http_methods(["POST"])
def refetch(request):
    url = request.POST.get("url")
    if not url:
        raise BadRequest(_("Invalid URL"))
    site = SiteManager.get_site_by_url(url, detect_redirection=False)
    if not site:
        raise BadRequest(_("Unsupported URL"))
    resource = ExternalResource.objects.filter(url=url).first()
    if resource and resource.item and not resource.item.is_editable_by(request.user):
        raise PermissionDenied(_("Editing this item is restricted."))
    return fetch(request, url, site, True)
