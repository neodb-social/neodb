import logging
from django.utils.translation import gettext_lazy as _
from catalog.common.sites import SiteManager
from ..models import TVSeason, Item
from django.conf import settings
import django_rq
from rq.job import Job
from django.core.cache import cache
import hashlib
from .typesense import Indexer as TypeSenseIndexer
from auditlog.context import set_actor

# from .meilisearch import Indexer as MeiliSearchIndexer

_logger = logging.getLogger(__name__)


class DbIndexer:
    @classmethod
    def search(cls, q, page=1, category=None, tag=None, sort=None):
        result = lambda: None
        result.items = Item.objects.filter(title__contains=q)[:10]
        result.num_pages = 1
        result.count = len(result.items)
        return result

    @classmethod
    def update_model_indexable(cls, model):
        pass


# if settings.SEARCH_BACKEND == "MEILISEARCH":
#
# el
if settings.SEARCH_BACKEND == "TYPESENSE":
    Indexer = TypeSenseIndexer
else:
    Indexer = DbIndexer


def query_index(keywords, category=None, tag=None, page=1, prepare_external=True):
    if page < 1 or page > 99 or (not tag and isinstance(keywords, str) and len(keywords) < 2):
        return [], 0, 0, []
    result = Indexer.search(keywords, page=page, category=category, tag=tag)
    keys = set()
    items = []
    duplicated_items = []
    urls = []
    for i in result.items:
        if i.is_deleted or i.merged_to_item:  # only happen if index is delayed
            continue
        if i.class_name == "work":  # TODO: add searchable_item_class global config
            continue
        my_key = (
            [i.isbn]
            if hasattr(i, "isbn")
            else ([i.imdb_code] if hasattr(i, "imdb_code") else [])
        )
        if hasattr(i, "works"):
            my_key += [i[0] for i in i.works.all().values_list("id")]
        if len(my_key):
            l = len(keys) + len(my_key)
            keys.update(my_key)
            # check and skip dup with same imdb or isbn or works id
            if len(keys) < l:
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
        cache_key = f"search_{category}_{keywords}"
        urls = list(set(cache.get(cache_key, []) + urls))
        cache.set(cache_key, urls, timeout=300)

    return items, result.num_pages, result.count, duplicated_items


def enqueue_fetch(url, is_refetch, user=None):
    job_id = "fetch_" + hashlib.md5(url.encode()).hexdigest()
    in_progress = False
    try:
        job = Job.fetch(id=job_id, connection=django_rq.get_connection("fetch"))
        in_progress = job.get_status() in ["queued", "started"]
    except:
        in_progress = False
    if not in_progress:
        django_rq.get_queue("fetch").enqueue(
            _fetch_task, url, is_refetch, user, job_id=job_id
        )
    return job_id


def _fetch_task(url, is_refetch, user):
    item_url = "-"
    with set_actor(user):
        try:
            site = SiteManager.get_site_by_url(url)
            if not site:
                return None
            site.get_resource_ready(ignore_existing_content=is_refetch)
            item = site.get_item()
            if item:
                _logger.info(f"fetched {url} {item.url} {item}")
                item_url = item.url
            else:
                _logger.error(f"fetch {url} failed")
        except Exception as e:
            _logger.error(f"fetch {url} error {e}")
        return item_url
