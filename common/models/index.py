import re
from functools import cached_property
from time import sleep
from typing import Iterable, Self

import typesense
from django.conf import settings
from loguru import logger
from typesense.collection import Collection
from typesense.exceptions import ObjectNotFound


class QueryParser:
    fields = ["sort"]
    default_search_params = {
        "q": "",
        "filter_by": "",
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
            r"\b(?P<field>" + "|".join(cls.fields) + r")\s*:(?P<value>[^ ]+)", re.I
        )

    def __init__(self, query: str, page: int = 1, page_size: int = 0):
        """Parse fields from a query string, subclass should define and use these fields"""
        self.raw_query = str(query) if query else ""
        if self.fields:
            r = self.re()
            self.q = r.sub("", query).strip()
            self.parsed_fields = {
                m.group("field").strip().lower(): m.group("value").strip().lower()
                for m in r.finditer(query)
            }
        else:
            self.q = query.strip()
            self.parsed_fields = {}
        self.page = page
        self.page_size = page_size
        self.filter_by = {}
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
        if self.filter_by:
            filters = []
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
            params["filter_by"] = " && ".join(filters)
        if self.query_by:
            params["query_by"] = ",".join(self.query_by)
        if self.sort_by:
            params["sort_by"] = ",".join(self.sort_by)
        return params


class SearchResult:
    def __init__(self, index: "Index", response: dict):
        self.index = index
        self.response = response
        self.page_size = response["request_params"]["per_page"]
        self.total = response["found"]
        self.page = response["page"]
        self.pages = (self.total + self.page_size - 1) // self.page_size

    def __repr__(self):
        return f"SearchResult(search '{self.response['request_params']['q']}', found {self.response['found']} out of {self.response['out_of']}, page {self.response['page']})"

    def __str__(self):
        return f"SearchResult(search '{self.response['request_params']['q']}', found {self.response['found']} out of {self.response['out_of']}, page {self.response['page']})"

    def get_facet(self, field):
        f = next(
            (f for f in self.response["facet_counts"] if f["field_name"] == field),
            None,
        )
        if not f:
            return {}
        return {v["value"]: v["count"] for v in f["counts"]}

    def __bool__(self):
        return len(self.response["hits"]) > 0

    def __len__(self):
        return len(self.response["hits"])

    def __iter__(self):
        return iter(self.response["hits"])

    def __getitem__(self, key):
        return self.response["hits"][key]

    def __contains__(self, item):
        return item in self.response["hits"]


class Index:
    name = ""  # must be set in subclass
    schema = {"fields": []}  # must be set in subclass
    search_result_class = SearchResult

    _instance = None
    _client: typesense.Client

    @classmethod
    def instance(cls) -> Self:
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get_client(cls):
        return typesense.Client(settings.TYPESENSE_CONNECTION)

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
    def get_schema(cls) -> dict:
        cname = settings.INDEX_ALIASES.get(
            cls.name + "_write"
        ) or settings.INDEX_ALIASES.get(cls.name, cls.name)
        schema = {"name": cname}
        schema.update(cls.schema)
        return schema

    def check(self) -> dict:
        if not self._client.operations.is_healthy():
            raise ValueError("Typesense: server not healthy")
        return self.read_collection.retrieve()

    def create_collection(self):
        self._client.collections.create(self.get_schema())

    def delete_collection(self):
        self.write_collection.delete()

    def update_schema(self, schema: dict):
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

    def replace_docs(self, docs: Iterable[dict]):
        if not docs:
            return False
        rs = self.write_collection.documents.import_(docs, {"action": "upsert"})
        for r in rs:
            e = r.get("error", None)
            if e:
                logger.error(f"Typesense: {self.name} import error {e}")
                if settings.DEBUG:
                    logger.error(f"Typesense: {r}")

    def insert_docs(self, docs: Iterable[dict]):
        if not docs:
            return False
        rs = self.write_collection.documents.import_(docs)
        for r in rs:
            e = r.get("error", None)
            if e:
                logger.error(f"Typesense: {self.name} import error {e}")
                if settings.DEBUG:
                    logger.error(f"Typesense: {r}")

    def delete_docs(self, field: str, values: list[int] | str) -> int:
        v: str = (
            ("[" + ",".join(map(str, values)) + "]")
            if isinstance(values, list)
            else values
        )
        q = {"filter_by": f"{field}:{v}"}
        r = self.write_collection.documents.delete(q)
        return (r or {}).get("num_deleted", 0)

    def patch_docs(self, partial_doc: dict, doc_filter: str):
        self.write_collection.documents.update(partial_doc, {"filter_by": doc_filter})

    def search(
        self,
        query: QueryParser,
    ) -> SearchResult:
        params = query.to_search_params()
        if settings.DEBUG:
            logger.debug(f"Typesense: search {self.name} {params}")
        r = self.read_collection.documents.search(params)
        sr = self.search_result_class(self, r)
        if settings.DEBUG:
            logger.debug(f"Typesense: search result {sr}")
        return sr
