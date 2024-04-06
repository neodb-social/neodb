import logging
import re

import django_rq
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import BadRequest
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from rq.job import Job

from catalog.common.models import ItemCategory, SiteName
from catalog.common.sites import AbstractSite, SiteManager
from common.config import PAGE_LINK_NUMBER
from common.utils import (
    HTTPResponseHXRedirect,
    PageLinksGenerator,
    user_identity_required,
)
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
        retry = int(request.GET.get("retry", 0)) + 1
        if retry > 10:
            return render(request, "_fetch_failed.html")
        else:
            return render(
                request,
                "_fetch_refresh.html",
                {"job_id": job_id, "retry": retry, "delay": retry * 2},
            )


def fetch(request, url, is_refetch: bool = False, site: AbstractSite | None = None):
    if not site:
        site = SiteManager.get_site_by_url(url)
        if not site:
            raise BadRequest()
    item = site.get_item()
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
            "site": site,
            "sites": SiteName.labels,
            "job_id": job_id,
        },
    )


def visible_categories(request):
    vc = request.session.get("p_categories", None)
    if vc is None:
        vc = [
            x
            for x in item_categories()
            if x.value
            not in (
                request.user.preference.hidden_categories
                if request.user.is_authenticated
                else []
            )
        ]
        request.session["p_categories"] = vc
    return vc


@user_identity_required
def search(request):
    keywords = request.GET.get("q", default="").strip()
    if re.match(r"^[@＠]", keywords):
        return query_identity(request, keywords.replace("＠", "@"))
    category = request.GET.get("c", default="all").strip().lower()
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
    p = request.GET.get("page", default="1")
    p = int(p) if p.isdigit() else 1
    if not (keywords or tag):
        return render(
            request,
            "search_results.html",
            {
                "items": None,
                "sites": SiteName.labels,
            },
        )

    if keywords.find("://") > 0:
        host = keywords.split("://")[1].split("/")[0]
        if host in settings.SITE_DOMAINS:
            return redirect(keywords)
        site = SiteManager.get_site_by_url(keywords)
        if site:
            return fetch(request, keywords, False, site)
        if request.GET.get("r"):
            return redirect(keywords)

    items, num_pages, __, dup_items = query_index(keywords, categories, tag, p)
    return render(
        request,
        "search_results.html",
        {
            "items": items,
            "dup_items": dup_items,
            "pagination": PageLinksGenerator(p, num_pages, request.GET),
            "sites": SiteName.labels,
            "hide_category": hide_category,
        },
    )


@login_required
def external_search(request):
    category = request.GET.get("c", default="all").strip().lower()
    if category == "all":
        category = None
    keywords = request.GET.get("q", default="").strip()
    page_number = int(request.GET.get("page", default=1))
    items = ExternalSources.search(category, keywords, page_number) if keywords else []
    cache_key = f"search_{category if category!='movietv' else 'movie,tv'}_{keywords}"
    dedupe_urls = cache.get(cache_key, [])
    items = [i for i in items if i.source_url not in dedupe_urls]

    return render(
        request,
        "external_search_results.html",
        {
            "external_items": items,
        },
    )


@login_required
def refetch(request):
    if request.method != "POST":
        raise BadRequest()
    url = request.POST.get("url")
    if not url:
        raise BadRequest()
    return fetch(request, url, True)
