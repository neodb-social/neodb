from datetime import timedelta
from functools import cached_property, reduce
from typing import TYPE_CHECKING, Iterable

import django_rq
from django_redis import get_redis_connection
from loguru import logger
from rq.job import Job

from common.models.misc import int_
from common.search import Index, QueryParser, SearchResult

if TYPE_CHECKING:
    from catalog.models import Item

_PENDING_INDEX_KEY = "pending_catalog_index_ids"
_PENDING_INDEX_QUEUE = "import"
_PENDING_INDEX_JOB_ID = "pending_catalog_index_flush"


def _update_catalog_index_task():
    conn = get_redis_connection("default")
    item_ids = conn.spop(_PENDING_INDEX_KEY, 1000)
    updated = 0
    index = CatalogIndex.instance()
    while item_ids:
        index.replace_items([int(i) for i in item_ids])
        updated += len(item_ids)
        item_ids = conn.spop(_PENDING_INDEX_KEY, 1000)
    logger.info(f"Catalog index updated for {updated} items")


def _cat_to_class(cat: str) -> list[str]:
    from catalog.models import ItemCategory, item_categories

    return [c.__name__ for c in item_categories().get(ItemCategory(cat), [])]


class CatalogQueryParser(QueryParser):
    fields = [
        "tag",
        "category",
        "format",
        "genre",
        "people",
        "company",
        "year",
        "language",
    ]
    default_search_params = {
        "query_by": "title, people, company, lookup_id, extra_title",
        "sort_by": "_text_match(bucket_size:20):desc,mark_count:desc",
        "per_page": 20,
        "include_fields": "id, item_id",
        "highlight_fields": "",
        "facet_by": "item_class",
    }

    def __init__(
        self,
        query: str,
        page: int = 1,
        page_size: int = 0,
        filter_categories=[],
        exclude_categories=[],
    ):
        from catalog.models import item_categories

        super().__init__(query, page, page_size)

        # each page will be sorted by relevance, then popularity within page
        if page_size:
            self.sort_by = [
                f"_text_match(bucket_size:{page_size}):desc",
                "mark_count:desc",
            ]

        # parse filters from query string
        for field in ["tag", "format", "genre", "people", "company", "language"]:
            v = [i for i in set(self.parsed_fields.get(field, "").split(",")) if i]
            if v:
                self.filter_by[field] = v

        # parse categories from query string or search option
        v = [
            i for i in set(self.parsed_fields.get("category", "").split(",")) if i
        ] or filter_categories
        if v:
            cats = {
                c.value: [ic.__name__ for ic in cl]
                for c, cl in item_categories().items()
            }
            v = list(set(v) & cats.keys())
            v = reduce(lambda a, b: a + b, [cats[i] for i in v], [])
        if v:
            self.filter_by["item_class"] = v
        elif exclude_categories:
            # apply exclude categories if no categories are specified
            cs = reduce(
                lambda a, b: a + b, [_cat_to_class(c) for c in exclude_categories], []
            )
            self.exclude("item_class", cs)

        # parse date filter from query string
        v = self.parsed_fields.get("year", "").split("..")
        if len(v) == 2:
            start = int_(v[0])
            end = int_(v[1])
            if start and end:
                self.filter_by["date"] = [f"{start * 10000}..{end * 10000 + 9999}"]
        elif len(v) == 1:
            year = int_(v[0])
            if year:
                self.filter_by["date"] = [f"{year * 10000}..{year * 10000 + 9999}"]


class CatalogSearchResult(SearchResult):
    @property
    def facet_by_item_class(self):
        return self.get_facet("item_class")

    @cached_property
    def items(self):
        from catalog.models import Item

        if not self:
            return []
        ids = [int(hit["document"]["id"]) for hit in self.response["hits"]]
        return Item.get_final_items(Item.get_by_ids(ids))

    def __iter__(self):  # type:ignore
        return iter(self.items)

    def __getitem__(self, key):
        return self.items[key]

    def __contains__(self, item):
        return item in self.items


class CatalogIndex(Index):
    name = "catalog"
    schema = {
        "fields": [
            {
                "name": "item_id",
                "type": "int64[]",
                "sort": False,
            },
            {
                "name": "item_class",
                "type": "string",
                "facet": True,
            },
            {
                "name": "date",
                "type": "int32[]",  # as YYYYMMDD, MM/DD can be 00
                "facet": True,
                "optional": True,
            },
            {
                "name": "lookup_id",
                "type": "string[]",
                "optional": True,
            },
            {
                "name": "language",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },
            {
                "name": "title",
                "locale": "zh",
                "type": "string[]",
            },
            {
                "name": "extra_title",
                "locale": "zh",
                "type": "string[]",
                "optional": True,
            },
            {
                "name": "people",
                "locale": "zh",
                "type": "string[]",
                "optional": True,
            },
            {
                "name": "company",
                "locale": "zh",
                "type": "string[]",
                "optional": True,
            },
            {
                "name": "genre",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },
            {
                "name": "format",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },
            {
                "name": "mark_count",
                "type": "int64",
                "optional": True,
            },
            {
                "name": "tag",
                "locale": "zh",
                "type": "string[]",
                "optional": True,
            },
            {"name": ".*", "optional": True, "locale": "zh", "type": "auto"},
        ]
    }
    search_result_class = CatalogSearchResult

    @classmethod
    def items_to_docs(cls, items: "Iterable[Item]") -> list[dict]:
        docs = [i.to_indexable_doc() for i in items]
        return [d for d in docs if d]

    def delete_all(self):
        return self.delete_docs("id", "*")

    def delete(self, item_ids):
        return self.delete_docs("id", item_ids)

    def replace_items(self, item_ids):
        from catalog.models import Item

        items = Item.objects.filter(pk__in=item_ids)
        docs = [
            i.to_indexable_doc()
            for i in items
            if not i.is_deleted and not i.merged_to_item_id
        ]
        if docs:
            self.replace_docs(docs)
        if len(docs) < len(item_ids):
            deletes = set(item_ids) - set([i.pk for i in items])
            self.delete_docs("item_id", deletes)

    def replace_item(self, item: "Item"):
        if not item.pk:
            logger.error(f"Indexing {item} but no pk")
            return
        if item.is_deleted or item.merged_to_item_id:
            self.delete_docs("id", item.pk)
        else:
            doc = item.to_indexable_doc()
            self.replace_docs([doc])

    @classmethod
    def enqueue_replace_items(cls, item_ids: list[int]):
        if not item_ids:
            return
        get_redis_connection("default").sadd(_PENDING_INDEX_KEY, *item_ids)
        try:
            job = Job.fetch(
                id=_PENDING_INDEX_JOB_ID,
                connection=django_rq.get_connection(_PENDING_INDEX_QUEUE),
            )
            if job.get_status() in ["queued", "scheduled"]:
                job.cancel()
        except Exception:
            pass
        # using rq's built-in scheduler here, it can be switched to other similar implementations
        django_rq.get_queue(_PENDING_INDEX_QUEUE).enqueue_in(
            timedelta(seconds=2),
            _update_catalog_index_task,
            job_id=_PENDING_INDEX_JOB_ID,
        )

    def delete_item(self, item: "Item"):
        if item.pk:
            self.delete_docs("item_id", item.pk)

    def search(
        self,
        query,
    ) -> CatalogSearchResult:
        r = super().search(query)
        return r  # type:ignore
