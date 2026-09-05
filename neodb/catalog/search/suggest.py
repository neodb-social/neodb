"""Typeahead suggestions rendered straight from index hits.

The regular search path loads every hit from Postgres. Suggestions run on
each keystroke, so the row is built from fields stored in the index
(``uuid``, ``display_title``/``display_name``, ``cover``). Only documents
indexed before those fields existed fall back to one database query.
"""

import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any, Callable, Sequence, cast

from django.conf import settings
from django.db.models import ImageField

from common.search import SearchResult

from .index import CatalogIndex, CatalogQueryParser
from .people_index import PeopleIndex, PeopleQueryParser

if TYPE_CHECKING:
    from catalog.models import Item, ItemCategory

SUGGEST_LIMIT = 8
SUGGEST_MIN_LENGTH = 2
SUGGEST_MAX_LENGTH = 100
_CJK = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff]")

# https://typesense.org/docs/latest/api/search.html#ranking-and-sorting-parameters
_SUGGEST_PARAMS: dict[str, Any] = {
    "per_page": SUGGEST_LIMIT,
    "num_typos": 1,
    "drop_tokens_threshold": 0,
    "exhaustive_search": False,
    "search_cutoff_ms": 50,
    "highlight_fields": "",
}


class CatalogSuggestParser(CatalogQueryParser):
    max_pages = 1
    default_search_params = {
        "query_by": "title, people, company, lookup_id, extra_title",
        "sort_by": f"_text_match(bucket_size:{SUGGEST_LIMIT}):desc,mark_count:desc",
        "include_fields": "id, item_class, title, uuid, display_title, cover",
        **_SUGGEST_PARAMS,
    }


class PeopleSuggestParser(PeopleQueryParser):
    max_pages = 1
    default_search_params = {
        "query_by": "name, lookup_id",
        "sort_by": f"_text_match(bucket_size:{SUGGEST_LIMIT}):desc,credit_count:desc",
        "include_fields": "id, people_type, name, uuid, display_name, cover",
        **_SUGGEST_PARAMS,
    }


@dataclass
class Suggestion:
    url: str
    title: str
    category: str
    cover_url: str | None = None
    # the indexed title that matched the query, when it is not ``title``
    matched: str = ""


@cache
def _item_classes() -> dict[str, type["Item"]]:
    from catalog.models import Item

    return {cls.__name__: cls for cls in Item.__subclasses__()}


def _cover_url(name: str | None) -> str | None:
    from catalog.models import Item

    if not name or name == settings.DEFAULT_ITEM_COVER:
        return None
    url = cast(ImageField, Item._meta.get_field("cover")).storage.url(name)
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{settings.SITE_INFO['site_url']}{url}"


def _matched_title(query: str, titles: Sequence[str], title: str) -> str:
    q = query.lower()
    for t in titles:
        if t != title and q in t.lower():
            return t
    return ""


def _people_label(people_type: str | None) -> str:
    from catalog.models import PeopleType

    if people_type in PeopleType.values:
        return str(PeopleType(people_type).label)
    return ""


def _row_from_item(item: "Item") -> Suggestion:
    from catalog.models import People

    category = (
        _people_label(item.people_type)
        if isinstance(item, People)
        else str(item.category.label)
    )
    return Suggestion(
        url=item.url,
        title=item.display_title,
        category=category,
        cover_url=_cover_url(item.cover.name),
    )


def _rows(
    r: SearchResult,
    from_doc: Callable[[dict], Suggestion | None],
    load: Callable[[list[int]], "Sequence[Item]"],
) -> list[Suggestion]:
    """Build one row per hit. A hit whose document predates the suggestion
    fields is loaded from the database instead, keeping the hit order."""
    if r.error:
        return []
    rows: list[Suggestion | int] = []
    stale: list[int] = []
    for hit in r.response.get("hits", []):
        doc = hit["document"]
        row = from_doc(doc) if doc.get("uuid") else None
        if row is None:
            pk = int(doc["id"])
            stale.append(pk)
            rows.append(pk)
        else:
            rows.append(row)
    loaded = {i.pk: _row_from_item(i) for i in load(stale)} if stale else {}
    return [
        row if isinstance(row, Suggestion) else loaded[row]
        for row in rows
        if isinstance(row, Suggestion) or row in loaded
    ]


def _valid_query(keywords: str) -> bool:
    # one CJK character is a whole token, so it is enough to suggest on
    if len(keywords) == 1:
        return bool(_CJK.search(keywords))
    return SUGGEST_MIN_LENGTH <= len(keywords) <= SUGGEST_MAX_LENGTH


def suggest_items(
    keywords: str,
    categories: "Sequence[ItemCategory] | None" = None,
    exclude_categories: "Sequence[ItemCategory] | None" = None,
) -> list[Suggestion]:
    from catalog.models import Item

    if not _valid_query(keywords):
        return []
    q = CatalogSuggestParser(
        keywords,
        page_size=SUGGEST_LIMIT,
        filter_categories=list(categories or []),
        exclude_categories=list(exclude_categories or []),
    )
    if not q:
        return []
    classes = _item_classes()

    def from_doc(doc: dict) -> Suggestion | None:
        cls = classes.get(doc.get("item_class", ""))
        if cls is None:
            return None
        title = doc.get("display_title") or ""
        return Suggestion(
            url=f"/{cls.url_path}/{doc['uuid']}",
            title=title,
            category=str(cls.category.label),
            cover_url=_cover_url(doc.get("cover")),
            matched=_matched_title(q.q, doc.get("title") or [], title),
        )

    return _rows(
        CatalogIndex.instance().search(q),
        from_doc,
        lambda pks: list(Item.objects.filter(pk__in=pks)),
    )


def suggest_people(keywords: str) -> list[Suggestion]:
    from catalog.models import People, PeopleType

    if not _valid_query(keywords):
        return []
    q = PeopleSuggestParser(keywords, page_size=SUGGEST_LIMIT)
    if not q:
        return []

    def from_doc(doc: dict) -> Suggestion:
        people_type = doc.get("people_type")
        path = (
            People.url_path_organization
            if people_type == PeopleType.ORGANIZATION
            else People.url_path_person
        )
        title = doc.get("display_name") or ""
        return Suggestion(
            url=f"/{path}/{doc['uuid']}",
            title=title,
            category=_people_label(people_type),
            cover_url=_cover_url(doc.get("cover")),
            matched=_matched_title(q.q, doc.get("name") or [], title),
        )

    return _rows(
        PeopleIndex.instance().search(q),
        from_doc,
        lambda pks: list(People.objects.filter(pk__in=pks)),
    )
