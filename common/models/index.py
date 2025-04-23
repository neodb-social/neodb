import re
from functools import cached_property
from time import sleep
from typing import Iterable, List, Self

from django.conf import settings
from loguru import logger
from requests import RequestException
from typesense.client import Client
from typesense.collection import Collection
from typesense.exceptions import ObjectNotFound
from typesense.types.collection import (
    CollectionCreateSchema,
    CollectionSchema,
    CollectionUpdateSchema,
)
from typesense.types.document import SearchResponse


class QueryParser:
    fields = ["sort"]
    default_search_params = {
        "q": "",
        "query_by": "",
        "sort_by": "",
        "per_page": 20,
        "include_fields": "id",
        "highlight_fields": "",
    }  # https://typesense.org/docs/latest/api/search.html#search-parameters
    max_pages = 100

    @classmethod
    def re(cls):
        return re.compile(
            r"\b(?P<field>" + "|".join(cls.fields) + r')\s*:(?P<value>[^ "]+|"[^"]+")',
            re.I,
        )

    def __init__(self, query: str, page: int = 1, page_size: int = 0):
        """Parse fields from a query string, subclass should define and use these fields"""
        self.raw_query = str(query) if query else ""
        if self.fields:
            r = self.re()
            self.q = r.sub("", self.raw_query).strip()
            self.parsed_fields = {
                m.group("field").strip().lower(): m.group("value").strip('  "').lower()
                for m in r.finditer(self.raw_query)
                if m.group("value").strip('  "')
            }
        else:
            self.q = self.raw_query.strip()
            self.parsed_fields = {}
        self.page = page
        self.page_size = page_size
        self.filter_by = {}
        self.exclude_by = {}
        self.query_by = []
        self.sort_by = []

    def is_valid(self):
        """Check if the parsed query is valid"""
        return (
            self.page > 0
            and self.page <= self.max_pages
            and bool(self.q or self.filter_by)
        )

    def __bool__(self):
        return self.is_valid()

    def filter(self, field: str, value: list[int | str] | int | str):
        """Override a specific filter"""
        self.filter_by[field] = value if isinstance(value, list) else [value]

    def exclude(self, field: str, value: list[int] | list[str] | int | str):
        """Exclude a specific filter"""
        self.exclude_by[field] = value if isinstance(value, list) else [value]

    def sort(self, fields: list[str]):
        """Override the default sort fields"""
        self.sort_by = fields

    def to_search_params(self) -> dict:
        """Convert the parsed query to search parameters"""
        params = self.default_search_params.copy()
        params["q"] = self.q
        params["page"] = (
            self.page if self.page > 0 and self.page <= self.max_pages else 1
        )
        if self.page_size:
            params["per_page"] = self.page_size
        filters = []
        if self.filter_by:
            for field, values in self.filter_by.items():
                if field == "_":
                    filters += values
                elif values:
                    v = (
                        f"[{','.join(map(str, values))}]"
                        if len(values) > 1
                        else str(values[0])
                    )
                    filters.append(f"{field}:{v}")
        if self.exclude_by:
            for field, values in self.exclude_by.items():
                if values:
                    v = (
                        f"[{','.join(map(str, values))}]"
                        if len(values) > 1
                        else str(values[0])
                    )
                    filters.append(f"{field}:!={v}")
        if filters:
            params["filter_by"] = " && ".join(filters)
        if self.query_by:
            params["query_by"] = ",".join(self.query_by)
        if self.sort_by:
            params["sort_by"] = ",".join(self.sort_by)
        return params


class SearchResult:
    def __init__(self, index: "Index", response: SearchResponse):
        self.index = index
        self.response = response
        self.request_params = response.get("request_params", {})
        self.page_size = self.request_params.get("per_page", 1)
        self.total = response.get("found", 0)
        self.page = response.get("page", 1)
        self.code = response.get("code", 0)
        self.error = response.get("error", None)
        self.pages = (self.total + self.page_size - 1) // self.page_size

    def __repr__(self):
        return f"SearchResult(search '{self.request_params.get('q', '')}', found {self.total} out of {self.response.get('out_of', -1)}, page {self.page})"

    def __str__(self):
        return f"SearchResult(search '{self.request_params.get('q', '')}', found {self.total} out of {self.response.get('out_of', -1)}, page {self.page})"

    def get_facet(self, field):
        facets = self.response.get("facet_counts", [])
        f = next(
            (f for f in facets if f["field_name"] == field),
            None,
        )
        if not f:
            return {}
        return {v["value"]: v["count"] for v in f["counts"]}

    def __bool__(self):
        return len(self.response.get("hits", [])) > 0

    def __len__(self):
        return len(self.response.get("hits", []))

    def __iter__(self):
        return iter(self.response.get("hits", []))

    def __getitem__(self, key):
        return self.response.get("hits", [])[key]

    def __contains__(self, item):
        return item in self.response.get("hits", [])


class Index:
    name = ""  # must be set in subclass
    schema = {"fields": []}  # must be set in subclass
    search_result_class = SearchResult

    _instance = None
    _client: Client

    @classmethod
    def instance(cls) -> Self:
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get_client(cls):
        return Client(settings.TYPESENSE_CONNECTION)

    def __init__(self, *args, **kwargs):
        self._client = self.get_client()

    def _get_collection(self, for_write=False) -> Collection:
        global _cached_collections
        collection_id = self.name + ("_write" if for_write else "_read")
        cname = settings.INDEX_ALIASES.get(collection_id) or settings.INDEX_ALIASES.get(
            self.name, self.name
        )
        collection = self._client.collections[cname]
        if not collection:
            raise KeyError(f"Typesense: collection {collection_id} not found")
        return collection

    @cached_property
    def read_collection(self) -> Collection:
        return self._get_collection()

    @cached_property
    def write_collection(self) -> Collection:
        return self._get_collection(True)

    @classmethod
    def get_schema(cls) -> CollectionCreateSchema:
        cname = settings.INDEX_ALIASES.get(
            cls.name + "_write"
        ) or settings.INDEX_ALIASES.get(cls.name, cls.name)
        schema = {"name": cname}
        schema.update(cls.schema)
        return schema  # type: ignore

    def check(self) -> CollectionSchema:
        if not self._client.operations.is_healthy():
            raise ValueError("Typesense: server not healthy")
        return self.read_collection.retrieve()

    def create_collection(self):
        self._client.collections.create(self.get_schema())

    def delete_collection(self):
        self.write_collection.delete()

    def update_schema(self, schema: CollectionUpdateSchema):
        self.write_collection.update(schema)

    def initialize_collection(self, max_wait=5) -> bool:
        try:
            wait = max_wait
            while not self._client.operations.is_healthy() and wait:
                logger.warning("Typesense: server not healthy")
                sleep(1)
                wait -= 1
            if not wait:
                logger.error("Typesense: timeout waiting for server")
                return False
            cname = settings.INDEX_ALIASES.get(
                self.name + "_write"
            ) or settings.INDEX_ALIASES.get(self.name, self.name)
            collection = self._client.collections[cname]
            if collection:
                try:
                    i = collection.retrieve()
                    logger.debug(f"Typesense: {cname} has {i['num_documents']} docs")
                except ObjectNotFound:
                    self.create_collection()
                    logger.info(f"Typesense: {cname} created")
                return True
            logger.error("Typesense: server unknown error")
        except Exception as e:
            logger.error(f"Typesense: server error {e}")
        return False

    def replace_docs(self, docs: List[dict]):
        docs = [doc for doc in docs if doc]
        if not docs:
            return False
        rs = self.write_collection.documents.import_(docs, {"action": "upsert"})
        for r in rs:
            e = r.get("error", None)
            if e:
                logger.error(f"Typesense: {self.name} import error {e}")
                if settings.DEBUG or settings.TESTING:
                    logger.error(f"Typesense: {docs}")
                    logger.error(f"Typesense: {r}")

    def insert_docs(self, docs: List[dict]):
        if not docs:
            return False
        rs = self.write_collection.documents.import_(docs)
        for r in rs:
            e = r.get("error", None)
            if e:
                logger.error(f"Typesense: {self.name} import error {e}")
                if settings.DEBUG:
                    logger.error(f"Typesense: {r}")

    def delete_docs(self, field: str, values: Iterable[int | str] | str) -> int:
        v: str = (
            ("[" + ",".join(map(str, values)) + "]")
            if isinstance(values, Iterable)
            else values
        )
        r = self.write_collection.documents.delete({"filter_by": f"{field}:{v}"})
        return (r or {}).get("num_deleted", 0)

    def patch_docs(self, partial_doc: dict, doc_filter: str):
        self.write_collection.documents.update(partial_doc, {"filter_by": doc_filter})

    def get_doc(self, doc_id: int | str):
        return self.read_collection.documents[str(doc_id)].retrieve()

    def search(
        self,
        query: QueryParser,
    ) -> SearchResult:
        params = query.to_search_params()
        if settings.DEBUG:
            logger.debug(f"Typesense: search {self.name} {params}")
        try:
            # use multi_search as typesense limits query size for normal search
            r = self._client.multi_search.perform(
                {"searches": [params]},  # type: ignore
                {"collection": self.read_collection.name},  # type: ignore
            )
        except RequestException as e:
            logger.error(f"Typesense: search error {e}")
            return self.search_result_class(self, {"error": str(e), "code": -1})  # type:ignore
        sr = self.search_result_class(self, r["results"][0])
        if sr.error:
            logger.error(f"Typesense: search error {sr.error}")
        elif settings.DEBUG:
            logger.debug(f"Typesense: search result {sr}")
        return sr
