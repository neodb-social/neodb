import uuid
import logging
from django.core.exceptions import BadRequest
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils.translation import gettext_lazy as _
from django.http import HttpResponseRedirect
from catalog.common.models import SiteName
from catalog.common.sites import AbstractSite, SiteManager
from ..models import *
from django.conf import settings
from common.utils import PageLinksGenerator
from common.config import PAGE_LINK_NUMBER
import django_rq
from rq.job import Job
from .external import ExternalSources
from django.core.cache import cache
import hashlib
from .models import query_index, enqueue_fetch

_logger = logging.getLogger(__name__)


class HTTPResponseHXRedirect(HttpResponseRedirect):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self["HX-Redirect"] = self["Location"]

    status_code = 200


@login_required
def fetch_refresh(request, job_id):
    retry = request.GET
    try:
        job = Job.fetch(id=job_id, connection=django_rq.get_connection("fetch"))
        item_url = job.return_value()
    except:
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
                "__refetch__": [url, None],
            }
        )
    job_id = enqueue_fetch(url, is_refetch)
    return render(
        request,
        "fetch_pending.html",
        {
            "site": site,
            "sites": SiteName.labels,
            "job_id": job_id,
        },
    )


def search(request):
    category = request.GET.get("c", default="all").strip().lower()
    if category == "all":
        category = None
    keywords = request.GET.get("q", default="").strip()
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

    if request.user.is_authenticated and keywords.find("://") > 0:
        site = SiteManager.get_site_by_url(keywords)
        if site:
            return fetch(request, keywords, False, site)

    items, num_pages, _, dup_items = query_index(keywords, category, tag, p)
    return render(
        request,
        "search_results.html",
        {
            "items": items,
            "dup_items": dup_items,
            "pagination": PageLinksGenerator(PAGE_LINK_NUMBER, p, num_pages),
            "sites": SiteName.labels,
            "hide_category": category is not None and category != "movietv",
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
    cache_key = f"search_{category}_{keywords}"
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
