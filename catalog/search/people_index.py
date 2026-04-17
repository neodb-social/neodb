from datetime import timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Iterable

import django_rq
from django.db.models import Count
from django_redis import get_redis_connection
from loguru import logger
from rq.job import Job

from common.search import Index, QueryParser, SearchResult

if TYPE_CHECKING:
    from catalog.models import People


_PENDING_PEOPLE_INDEX_KEY = "pending_people_index_ids"
_PENDING_PEOPLE_INDEX_QUEUE = "import"
_PENDING_PEOPLE_INDEX_JOB_ID = "pending_people_index_flush"


def _update_people_index_task():
    conn = get_redis_connection("default")
    item_ids = conn.spop(_PENDING_PEOPLE_INDEX_KEY, 1000)
    updated = 0
    index = PeopleIndex.instance()
    while item_ids:
        index.replace_people([int(i) for i in item_ids])
        updated += len(item_ids)
        item_ids = conn.spop(_PENDING_PEOPLE_INDEX_KEY, 1000)
    logger.info(f"People index updated for {updated} items")


def _year_from_date(date_str: str | None) -> int | None:
    if not date_str:
        return None
    s = str(date_str).strip()
    if len(s) < 4 or not s[:4].isdigit():
        return None
    return int(s[:4])


class PeopleQueryParser(QueryParser):
    fields = ["type", "id"]
    default_search_params = {
        "query_by": "name, lookup_id",
        "sort_by": "_text_match(bucket_size:20):desc,credit_count:desc",
        "per_page": 20,
        "include_fields": "id, item_id",
        "highlight_fields": "",
        "facet_by": "people_type",
    }

    def __init__(
        self,
        query: str,
        page: int = 1,
        page_size: int = 0,
        people_type: str | None = None,
    ):
        super().__init__(query, page, page_size)

        if page_size:
            self.sort_by = [
                f"_text_match(bucket_size:{page_size}):desc",
                "credit_count:desc",
            ]

        v = [i for i in set(self.parsed_fields.get("type", "").split(",")) if i] or (
            [people_type] if people_type else []
        )
        v = [i for i in v if i in {"person", "organization"}]
        if v:
            self.filter_by["people_type"] = v

        v = self.parsed_fields.get("id", "").strip()
        if v and v.replace("-", "").replace("_", "").isalnum():
            self.filter("_", f"lookup_id:=`{v}`")


class PeopleSearchResult(SearchResult):
    @property
    def facet_by_people_type(self):
        return self.get_facet("people_type")

    @cached_property
    def items(self) -> list["People"]:
        from catalog.models import People

        if not self:
            return []
        ids = [int(hit["document"]["id"]) for hit in self.response["hits"]]
        people_by_id = {
            p.pk: p
            for p in People.objects.filter(
                pk__in=ids, is_deleted=False, merged_to_item__isnull=True
            )
        }
        return [people_by_id[i] for i in ids if i in people_by_id]

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, key):
        return self.items[key]

    def __contains__(self, item):
        return item in self.items


class PeopleIndex(Index):
    name = "people"
    schema = {
        "fields": [
            {
                "name": "item_id",
                "type": "int64[]",
                "sort": False,
            },
            {
                "name": "people_type",
                "type": "string",
                "facet": True,
            },
            {
                "name": "name",
                "locale": "zh",
                "type": "string[]",
            },
            {
                "name": "lookup_id",
                "type": "string[]",
                "optional": True,
            },
            {
                "name": "credit_count",
                "type": "int64",
                "optional": True,
            },
            {
                "name": "birth_year",
                "type": "int32",
                "optional": True,
                "facet": True,
            },
            {
                "name": "death_year",
                "type": "int32",
                "optional": True,
                "facet": True,
            },
        ]
    }
    search_result_class = PeopleSearchResult

    @classmethod
    def people_to_docs(cls, people: "Iterable[People]") -> list[dict]:
        docs = [cls.person_to_doc(p) for p in people]
        return [d for d in docs if d]

    @classmethod
    def person_to_doc(cls, person: "People") -> dict:
        if person.is_deleted or person.merged_to_item_id:
            return {}
        names = [n["text"] for n in (person.localized_name or []) if n.get("text")]
        if not names:
            return {}
        lookup_ids: list[str] = []
        for attr in ("imdb", "tmdb_person", "douban_personage"):
            v = getattr(person, attr, None)
            if v:
                lookup_ids.append(str(v))
        for res in person.external_resources.all():
            v = res.id_value
            if v and str(v) not in lookup_ids:
                lookup_ids.append(str(v))
        # Prefer annotated credit_count (set by bulk callers with .annotate())
        # to avoid an N+1 query per person during reindexing.
        annotated = getattr(person, "credit_count", None)
        credit_count = (
            annotated if isinstance(annotated, int) else person.credited_items.count()
        )
        doc: dict = {
            "id": str(person.pk),
            "item_id": [person.pk],
            "people_type": person.people_type,
            "name": names,
            "credit_count": credit_count,
        }
        if lookup_ids:
            doc["lookup_id"] = lookup_ids
        by = _year_from_date(getattr(person, "birth_date", ""))
        dy = _year_from_date(getattr(person, "death_date", ""))
        if by is not None:
            doc["birth_year"] = by
        if dy is not None:
            doc["death_year"] = dy
        return doc

    def delete_all(self):
        return self.delete_docs("id", "*")

    def delete(self, item_ids):
        return self.delete_docs("id", item_ids)

    def replace_people(self, item_ids: list[int]):
        from catalog.models import People

        people = list(
            People.objects.filter(pk__in=item_ids)
            .annotate(credit_count=Count("credited_items"))
            .prefetch_related("external_resources")
        )
        docs = [
            self.person_to_doc(p)
            for p in people
            if not p.is_deleted and not p.merged_to_item_id
        ]
        docs = [d for d in docs if d]
        if docs:
            self.replace_docs(docs)
        missing = set(item_ids) - {p.pk for p in people}
        orphaned = {
            p.pk
            for p in people
            if p.is_deleted or p.merged_to_item_id or not self.person_to_doc(p)
        }
        to_delete = missing | orphaned
        if to_delete:
            self.delete_docs("item_id", to_delete)

    def replace_person(self, person: "People"):
        if not person.pk:
            logger.error(f"Indexing {person} but no pk")
            return
        doc = self.person_to_doc(person)
        if not doc:
            self.delete_docs("item_id", person.pk)
            return
        self.replace_docs([doc])

    def delete_person(self, person: "People"):
        if person.pk:
            self.delete_docs("item_id", person.pk)

    @classmethod
    def enqueue_replace_people(cls, item_ids: list[int]):
        if not item_ids:
            return
        try:
            get_redis_connection("default").sadd(_PENDING_PEOPLE_INDEX_KEY, *item_ids)
            job = Job.fetch(
                id=_PENDING_PEOPLE_INDEX_JOB_ID,
                connection=django_rq.get_connection(_PENDING_PEOPLE_INDEX_QUEUE),
            )
            if job.get_status() in ["queued", "scheduled"]:
                job.cancel()
        except Exception:
            pass
        django_rq.get_queue(_PENDING_PEOPLE_INDEX_QUEUE).enqueue_in(
            timedelta(seconds=2),
            _update_people_index_task,
            job_id=_PENDING_PEOPLE_INDEX_JOB_ID,
        )

    def search(self, query) -> PeopleSearchResult:
        return super().search(query)  # type: ignore
