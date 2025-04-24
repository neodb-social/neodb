# pyright: reportFunctionMemberAccess=false
import hashlib
from urllib.parse import quote_plus

import django_rq
from auditlog.context import set_actor
from django.conf import settings
from django.core.cache import cache
from loguru import logger
from rq.job import Job

from catalog.common.downloaders import RESPONSE_CENSORSHIP, DownloadError
from catalog.common.models import ItemCategory, SiteName
from catalog.common.sites import SiteManager
from catalog.index import CatalogIndex, CatalogQueryParser

from ..models import TVSeason


class ExternalSearchResultItem:
    def __init__(
        self,
        category: ItemCategory | None,
        source_site: SiteName,
        source_url: str,
        title: str,
        subtitle: str,
        brief: str,
        cover_url: str,
    ):
        self.class_name = "base"
        self.category = category
        self.external_resources = {
            "all": [
                {
                    "url": source_url,
                    "site_name": source_site,
                    "site_label": source_site,
                }
            ]
        }
        self.source_site = source_site
        self.source_url = source_url
        self.display_title = title
        self.subtitle = subtitle
        self.display_description = brief
        self.cover_image_url = cover_url

    def __repr__(self):
        return f"[{self.category}] {self.display_title} {self.source_url}"

    @property
    def verbose_category_name(self):
        return self.category.label if self.category else ""

    @property
    def url(self):
        return f"/search?q={quote_plus(self.source_url)}"

    @property
    def scraped(self):
        return False


def query_index(
    keywords,
    categories=None,
    page=1,
    prepare_external=True,
    exclude_categories=None,
):
    if (
        page < 1
        or page > 99
        or (isinstance(keywords, str) and len(keywords) < 2)
        or len(keywords) > 100
    ):
        return [], 0, 0, [], {}, keywords
    args = {}
    if categories:
        args["filter_categories"] = categories
    if exclude_categories:
        args["exclude_categories"] = exclude_categories
    q = CatalogQueryParser(keywords, page, **args)
    if not q:
        return [], 0, 0, [], {}, keywords
    index = CatalogIndex.instance()
    r = index.search(q)
    keys = set()
    items = []
    duplicated_items = []
    urls = []
    for i in r.items:
        key = getattr(i, "isbn", getattr(i, "imdb_code", None))
        my_key = [key] if key else []
        if hasattr(i, "get_work"):
            work = i.get_work()  # type: ignore
            if work:
                my_key += [work.id]
        if len(my_key):
            sl = len(keys) + len(my_key)
            keys.update(my_key)
            # check and skip dup with same imdb or isbn or works id
            if len(keys) < sl:
                duplicated_items.append(i)
            else:
                items.append(i)
        else:
            items.append(i)
        for res in i.external_resources.all():
            urls.append(res.url)
    # hide show if its season exists
    seasons = [i for i in items if i.__class__ == TVSeason]
    for season in seasons:
        if season.show in items:
            duplicated_items.append(season.show)
            items.remove(season.show)

    if prepare_external:
        # store site url to avoid dups in external search
        cache_key = f"search_{','.join(categories or [])}_{keywords}"
        urls = list(set(cache.get(cache_key, []) + urls))
        cache.set(cache_key, urls, timeout=300)

    return items, r.pages, r.total, duplicated_items, r.facet_by_category, q.q


def get_fetch_lock(user, url):
    if user and user.is_authenticated:
        _fetch_lock_key = f"_fetch_lock:{user.id}"
        _fetch_lock_ttl = 1 if settings.DEBUG else 3
    else:
        _fetch_lock_key = "_fetch_lock"
        _fetch_lock_ttl = 1 if settings.DEBUG else 15
    if cache.get(_fetch_lock_key):
        return False
    cache.set(_fetch_lock_key, 1, timeout=_fetch_lock_ttl)
    # do not fetch the same url twice in 2 hours
    _fetch_lock_key = f"_fetch_lock:{url}"
    _fetch_lock_ttl = 1 if settings.DEBUG else 7200
    if cache.get(_fetch_lock_key):
        return False
    cache.set(_fetch_lock_key, 1, timeout=_fetch_lock_ttl)
    return True


def enqueue_fetch(url, is_refetch, user=None):
    job_id = "fetch_" + hashlib.md5(url.encode()).hexdigest()
    in_progress = False
    try:
        job = Job.fetch(id=job_id, connection=django_rq.get_connection("fetch"))
        in_progress = job.get_status() in ["queued", "started"]
    except Exception:
        in_progress = False
    if not in_progress:
        django_rq.get_queue("fetch").enqueue(
            _fetch_task, url, is_refetch, user, job_id=job_id
        )
    return job_id


def _fetch_task(url, is_refetch, user):
    item_url = "-"
    with set_actor(user if user and user.is_authenticated else None):
        try:
            site = SiteManager.get_site_by_url(url)
            if not site:
                return None
            site.get_resource_ready(ignore_existing_content=is_refetch)
            item = site.get_item()
            if item:
                logger.info(f"fetched {url} {item.url} {item}")
                item_url = item.url
            else:
                logger.error(f"fetch {url} failed")
        except DownloadError as e:
            if e.response_type != RESPONSE_CENSORSHIP:
                logger.error(f"fetch {url} error", extra={"exception": e})
        except Exception as e:
            logger.error(f"parse {url} error {e}", extra={"exception": e})
        return item_url
