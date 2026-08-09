import re
from functools import cached_property
from json import JSONDecodeError
from time import sleep
from typing import Iterable, Iterator, List, Self, cast

import httpx
from django.conf import settings
from loguru import logger
from requests import RequestException
from typesense.exceptions import ObjectNotFound, TypesenseClientError
from typesense.sync.client import Client
from typesense.sync.collection import Collection
from typesense.types.collection import (
    CollectionCreateSchema,
    CollectionSchema,
    CollectionUpdateSchema,
)
from typesense.types.document import MultiSearchCommonParameters, SearchResponse
from typesense.types.multi_search import MultiSearchRequestSchema

from common.models.site_config import SiteConfig

# Exceptions that any Typesense network operation may raise.
# typesense 2.x uses httpx for transport and, after exhausting node retries,
# re-raises the underlying httpx error (httpx.HTTPError and subclasses such as
# ConnectError and TimeoutException) -- which is neither a TypesenseClientError
# nor a requests.RequestException. JSONDecodeError can escape when the server
# returns a non-JSON body on a 2xx response. RequestException is kept defensively.
TYPESENSE_ERRORS = (
    RequestException,
    TypesenseClientError,
    httpx.HTTPError,
    JSONDecodeError,
)

# Typesense deletes by filter synchronously, and a large match set can hold up
# every other operation on the server for minutes; batch_size bounds how much
# it does at a time. The server default is tuned for speed, not for sharing the
# box with live indexing, so always pass one.
# https://typesense.org/docs/latest/api/documents.html#delete-by-query
DELETE_BATCH_SIZE = 100

# A filtered per-owner export comes back in milliseconds on a healthy server
# and is run once per identity in a loop, so it gets a short bound: a Typesense
# that still accepts connections but has stopped answering must not hold up
# each identity in turn for minutes.
EXPORT_TIMEOUT_SECONDS = 30

# Operations that answer only once the server has finished the whole job --
# delete-by-filter and the full-collection export -- take minutes on a large
# collection, against the few seconds the shared clients allow for search.
LONG_OP_TIMEOUT_SECONDS = 600

EXPORT_CONNECT_TIMEOUT_SECONDS = 5


def _backtick(s: str | int) -> str:
    """Escape a string with backticks for Typesense filter syntax"""
    return str(s) if isinstance(s, int) else f"`{str(s).replace('`', '\\`')}`"


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
    skip_backtick = []  # filter fields that should not be backticked
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
        self.facet_by = []

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
                    if field in self.skip_backtick:
                        v = (
                            f"[{','.join(str(x) for x in values)}]"
                            if len(values) > 1
                            else str(values[0])
                        )
                    else:
                        v = (
                            f"[{','.join(_backtick(x) for x in values)}]"
                            if len(values) > 1
                            else _backtick(values[0])
                        )
                    filters.append(f"{field}:{v}")
        if self.exclude_by:
            for field, values in self.exclude_by.items():
                if values:
                    if field in self.skip_backtick:
                        v = (
                            f"[{','.join(str(x) for x in values)}]"
                            if len(values) > 1
                            else str(values[0])
                        )
                    else:
                        v = (
                            f"[{','.join(_backtick(x) for x in values)}]"
                            if len(values) > 1
                            else _backtick(values[0])
                        )
                    filters.append(f"{field}:!={v}")
        if filters:
            params["filter_by"] = " && ".join(filters)
        if self.query_by:
            params["query_by"] = ",".join(self.query_by)
        if self.sort_by:
            params["sort_by"] = ",".join(self.sort_by)
        if self.facet_by:
            params["facet_by"] = ",".join(self.facet_by)
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

    @property
    def facet_by_category(self) -> dict[str, int]:
        from catalog.models import ItemCategory, item_categories

        item_class_facets = self.get_facet("item_class")

        # Initialize with all categories set to 0
        category_facets = {cat.value: 0 for cat in ItemCategory}

        if item_class_facets:
            # Map from class names to category values
            class_to_cat = {}
            for cat, classes in item_categories().items():
                for cls in classes:
                    class_to_cat[cls.__name__] = cat.value

            # Group facet counts by category
            for class_name, count in item_class_facets.items():
                if class_name in class_to_cat:
                    cat = class_to_cat[class_name]
                    category_facets[cat] += count

        return category_facets

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
    _read_client: Client
    _write_client: Client

    @classmethod
    def instance(cls) -> Self:
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get_client(cls, for_write: bool = False) -> Client:
        # Reads are on the request path, so they must never retry: on a timeout
        # the client retries the identical (slow) query immediately with no
        # backoff, turning one slow search into a burst of consecutive HTTP
        # calls that both stalls the page and piles load onto an already-slow
        # Typesense (NEODB-SOCIAL-7RV). Writes run in background jobs where
        # durability matters more than latency, so they retry a couple of times.
        return Client(
            {
                **settings.TYPESENSE_CONNECTION,
                "num_retries": 2 if for_write else 0,
            }
        )

    @classmethod
    def get_bulk_client(cls) -> Client:
        """Client for writes that answer only once the server is done.

        Delete-by-filter returns its count when the whole match set is gone,
        minutes on a large collection. Under the shared write client's
        search-sized timeout such a delete is abandoned and reissued twice with
        no pause (typesense-python parses retry_interval_seconds and never
        sleeps on it), so the server ends up running overlapping copies of the
        same delete while the caller is told 0 documents went. Retries are off
        here for the same reason: a delete that timed out is very likely still
        running, and firing another is precisely what must not happen. A delete
        that never lands is recoverable -- the next sync still sees the docs.
        """
        return Client(
            {
                **settings.TYPESENSE_CONNECTION,
                "connection_timeout_seconds": LONG_OP_TIMEOUT_SECONDS,
                "num_retries": 0,
            }
        )

    def __init__(self):
        self._read_client = self.get_client()
        self._write_client = self.get_client(for_write=True)
        self._bulk_client = self.get_bulk_client()

    def _get_collection(
        self, for_write=False, client: Client | None = None
    ) -> Collection:
        collection_id = self.name + ("_write" if for_write else "_read")
        cname = SiteConfig.system.index_aliases.get(
            collection_id
        ) or SiteConfig.system.index_aliases.get(self.name, self.name)
        client = client or (self._write_client if for_write else self._read_client)
        collection = client.collections[cname]
        if not collection:
            raise KeyError(f"Typesense: collection {collection_id} not found")
        return collection

    @cached_property
    def read_collection(self) -> Collection:
        return self._get_collection()

    @cached_property
    def write_collection(self) -> Collection:
        return self._get_collection(True)

    @cached_property
    def bulk_write_collection(self) -> Collection:
        """Write collection on the long-timeout, no-retry client."""
        return self._get_collection(True, self._bulk_client)

    @cached_property
    def _export_client(self) -> httpx.Client:
        """Long-lived client for streamed exports; see export_docs().

        Held for the life of the index so a sync that exports once per identity
        reuses the connection rather than opening a fresh socket per owner and
        leaving each in TIME_WAIT -- over a large remote estate that is both
        needless handshake load on the server and a way to run the client out
        of ephemeral ports. Timeouts are per request, not on the client.
        """
        return httpx.Client()

    @classmethod
    def get_schema(cls) -> CollectionCreateSchema:
        cname = SiteConfig.system.index_aliases.get(
            cls.name + "_write"
        ) or SiteConfig.system.index_aliases.get(cls.name, cls.name)
        schema = {"name": cname}
        schema.update(cls.schema)
        return schema  # type: ignore

    def check(self) -> CollectionSchema:
        if not self._read_client.operations.is_healthy():
            raise ValueError("Typesense: server not healthy")
        return self.read_collection.retrieve()

    def create_collection(self):
        self._write_client.collections.create(self.get_schema())

    def delete_collection(self):
        self.write_collection.delete()

    def update_schema(self, schema: CollectionUpdateSchema):
        self.write_collection.update(schema)

    def initialize_collection(self, max_wait=5) -> bool:
        try:
            wait = max_wait
            while not self._write_client.operations.is_healthy() and wait:
                logger.warning("Typesense: server not healthy")
                sleep(1)
                wait -= 1
            if not wait:
                logger.error("Typesense: timeout waiting for server")
                return False
            cname = SiteConfig.system.index_aliases.get(
                self.name + "_write"
            ) or SiteConfig.system.index_aliases.get(self.name, self.name)
            collection = self._write_client.collections[cname]
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
            logger.error(f"Typesense: initialization error {e}")
        return False

    def replace_docs(self, docs: List[dict]):
        docs = [doc for doc in docs if doc]
        if not docs:
            return 0
        try:
            rs = self.write_collection.documents.import_(docs, {"action": "upsert"})
        except TYPESENSE_ERRORS as e:
            logger.error(f"Typesense: error {e}")
            return 0
        c = 0
        for r in rs:
            e = r.get("error", None)
            if e:
                logger.error(f"Typesense: {self.name} import error {e}")
                if settings.DEBUG or settings.TESTING:
                    logger.error(f"Typesense: {docs}")
                    logger.error(f"Typesense: {r}")
            else:
                c += 1
        return c

    def insert_docs(self, docs: List[dict]) -> int:
        if not docs:
            return 0
        try:
            rs = self.write_collection.documents.import_(docs)
        except TYPESENSE_ERRORS as e:
            logger.error(f"Typesense: error {e}")
            return 0
        c = 0
        for r in rs:
            e = r.get("error", None)
            if e:
                logger.error(f"Typesense: {self.name} import error {e}")
                if settings.DEBUG:
                    logger.error(f"Typesense: {r}")
            else:
                c += 1
        return c

    def export_docs(
        self, params: dict, timeout: float = EXPORT_TIMEOUT_SECONDS
    ) -> Iterator[str]:
        """Stream the export endpoint, yielding one non-empty JSONL line at a time.

        This bypasses the shared client on purpose. That client applies
        connection_timeout_seconds -- sized for search, a few seconds -- as the
        httpx read timeout of every request including this one, and the write
        client retries twice with no pause in between (typesense-python reads
        retry_interval_seconds from the config and then never sleeps on it). An
        export that stalls once therefore re-runs the entire scan up to three
        times back to back, with every abandoned attempt still churning on the
        server. It also buffers the whole response into one string, which for a
        full-collection export is hundreds of MB.

        Raises the underlying error (all within TYPESENSE_ERRORS) on failure.
        """
        node = settings.TYPESENSE_CONNECTION["nodes"][0]
        url = (
            f"{node['protocol']}://{node['host']}:{node['port']}"
            f"/collections/{self.write_collection.name}/documents/export"
        )
        with self._export_client.stream(
            "GET",
            url,
            params=params,
            headers={"X-TYPESENSE-API-KEY": settings.TYPESENSE_CONNECTION["api_key"]},
            timeout=httpx.Timeout(timeout, connect=EXPORT_CONNECT_TIMEOUT_SECONDS),
        ) as r:
            if r.status_code < 200 or r.status_code >= 300:
                r.read()
                raise TypesenseClientError(
                    f"export failed: {r.status_code} {r.text[:200]}"
                )
            for line in r.iter_lines():
                if line:
                    yield line

    def delete_docs(
        self,
        field: str,
        values: Iterable[int | str] | int | str,
        batch_size: int = DELETE_BATCH_SIZE,
    ) -> int:
        v: str = (
            str(values)
            if isinstance(values, (str, int))
            else ("[" + ",".join(str(x) for x in values) + "]")
        )
        try:
            # bulk client, not the shared write one: batch_size makes the
            # server yield between batches but the response still waits for the
            # whole filter, so a short timeout here would abandon and reissue a
            # delete that is still running
            r = self.bulk_write_collection.documents.delete(
                {"filter_by": f"{field}:{v}", "batch_size": batch_size}
            )
        except TYPESENSE_ERRORS as e:
            logger.error(f"Typesense: error {e}")
            return 0
        return (r or {}).get("num_deleted", 0)

    def delete_all(self):
        raise NotImplementedError("Index.delete_all() must be implemented in subclass")

    def patch_docs(self, partial_doc: dict, doc_filter: str):
        try:
            self.write_collection.documents.update(
                partial_doc, {"filter_by": doc_filter}
            )
        except TYPESENSE_ERRORS as e:
            logger.error(f"Typesense: error {e}")

    def get_doc(self, doc_id: int | str) -> dict | None:
        try:
            return self.read_collection.documents[str(doc_id)].retrieve()
        except ObjectNotFound:
            # a missing document is an expected result, not a failure
            raise
        except TYPESENSE_ERRORS as e:
            logger.error(f"Typesense: error {e}")
            return None

    def _error_result(self, error: str) -> SearchResult:
        return self.search_result_class(self, {"error": error, "code": -1})  # type:ignore

    def search(
        self,
        query: QueryParser,
    ) -> SearchResult:
        params = query.to_search_params()
        if settings.DEBUG:
            logger.debug(f"Typesense: search {self.name} {params}")
        try:
            # use multi_search as typesense limits query size for normal search
            r = self._read_client.multi_search.perform(
                cast(MultiSearchRequestSchema, {"searches": [params]}),
                cast(
                    MultiSearchCommonParameters,
                    {"collection": self.read_collection.name},
                ),
            )
        except TYPESENSE_ERRORS as e:
            logger.error(f"Typesense: error {e}")
            return self._error_result(str(e))
        results = r.get("results") if isinstance(r, dict) else None
        if (
            not isinstance(results, list)
            or not results
            or not isinstance(results[0], dict)
        ):
            logger.error(f"Typesense: search {self.name} invalid response {r}")
            return self._error_result("invalid response")
        sr = self.search_result_class(self, results[0])
        if sr.error:
            logger.error(f"Typesense: search error {sr.error}")
        elif settings.DEBUG:
            logger.debug(f"Typesense: search result {sr}")
        return sr
