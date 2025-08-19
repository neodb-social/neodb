import re

import django_rq
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from rq.job import Job

from catalog.common.models import ItemCategory, SiteName
from catalog.common.sites import AbstractSite, SiteManager
from common.models import int_
from common.utils import (
    HTTPResponseHXRedirect,
    PageLinksGenerator,
    get_page_size_from_request,
    user_identity_required,
)
from journal.models import Tag
from journal.models.mark import Mark
from journal.models.rating import Rating
from users.views import query_identity

from ..models import *
from .external import ExternalSources
from .models import enqueue_fetch, get_fetch_lock, query_index


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


def visible_categories(request):
    if not hasattr(request, "user"):
        return []
    vc = request.session.get("p_categories", None)
    if vc is None:
        vc = [
            x
            for x in item_categories()
            if x.value
            not in (
                request.user.preference.hidden_categories
                if request.user.is_authenticated
                else settings.HIDDEN_CATEGORIES
            )
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

    if keywords.find("://") > 0:
        host = keywords.split("://")[1].split("/")[0]
        if host in settings.SITE_DOMAINS:
            return redirect(keywords)
        # skip detecting redirection to avoid timeout
        site = SiteManager.get_site_by_url(
            keywords, detect_redirection=False, detect_fallback=False
        )
        if site:
            return fetch(request, keywords, site, False)
        if request.GET.get("r"):
            return redirect(keywords)
        return fetch(request, keywords, None, False)

    if tag:
        redir = reverse("common:search") + f"?q=tag:{tag}"
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
    Rating.attach_to_items(items)
    if request.user.is_authenticated:
        Mark.attach_to_items(request.user.identity, items, request.user)
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


@login_required
def external_search(request):
    category = request.GET.get("c", default="all").strip().lower()
    keywords = request.GET.get("q", default="").strip()
    page_number = int_(request.GET.get("page"), 1)
    items = (
        ExternalSources.search(
            keywords, page_number, category, visible_categories(request)
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
    if (
        resource
        and resource.item
        and resource.item.is_protected
        and not request.user.is_staff
    ):
        raise PermissionDenied(_("Editing this item is restricted."))
    return fetch(request, url, site, True)
