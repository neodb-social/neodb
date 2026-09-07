"""Typeahead suggestions for the header search box.

Rows are built from what the search index already stores, so no index
schema change is needed. Only the item link and its cover are missing
there, and both live on the base ``catalog_item`` table: one flat query
per request fetches those two columns for the whole page of hits, without
the polymorphic join or the heavy ``metadata`` column that loading real
``Item`` objects would pull in.

The trade-off is that a row shows an indexed title rather than the
composed, per-viewer one: the index keeps every localized title without
its language tag, so the title that the query matched is shown instead.
"""

import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any, Sequence, cast
from uuid import UUID

from django.conf import settings
from django.core.signing import b62_encode
from django.db.models import ImageField

from common.models.misc import MISSING_COVER
from .index import CatalogIndex, CatalogQueryParser
from .people_index import PeopleIndex, PeopleQueryParser

if TYPE_CHECKING:
    from catalog.models import Item, ItemCategory

SUGGEST_LIMIT = 8
SUGGEST_MIN_LENGTH = 2
SUGGEST_MAX_LENGTH = 100
# one CJK character is a whole token, so it is enough to suggest on
_CJK = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff]")

# https://typesense.org/docs/latest/api/search.html#ranking-and-sorting-parameters
_SUGGEST_PARAMS: dict[str, Any] = {
    "per_page": SUGGEST_LIMIT,
    # A typeahead fires on every keystroke, so one query must stay cheap.
    # Typo tolerance belongs to full search: a token long enough to pass
    # min_len_1typo gets expanded here for no gain, because the next
    # keystroke corrects the typo anyway (NEODB-SOCIAL-7WD).
    "num_typos": 0,
    "drop_tokens_threshold": 0,
    "exhaustive_search": False,
    "search_cutoff_ms": 50,
    "highlight_fields": "",
}


class CatalogSuggestParser(CatalogQueryParser):
    max_pages = 1
    # Full search also matches `people` and `company`, but they are large
    # multi-valued fields, and prefix expansion over them made a two or three
    # character query as expensive as a whole page of full search. The
    # dropdown is a title typeahead; Enter still runs full search, which keeps
    # matching creators. `prefix` is positional against `query_by`, so the two
    # lists must stay the same length and in the same order.
    default_search_params = {
        "query_by": "title, extra_title, lookup_id",
        "prefix": "true,true,false",
        "sort_by": f"_text_match(bucket_size:{SUGGEST_LIMIT}):desc,mark_count:desc",
        "include_fields": "id, item_class, title",
        **_SUGGEST_PARAMS,
    }


class PeopleSuggestParser(PeopleQueryParser):
    max_pages = 1
    default_search_params = {
        "query_by": "name, lookup_id",
        "prefix": "true,false",
        "sort_by": f"_text_match(bucket_size:{SUGGEST_LIMIT}):desc,credit_count:desc",
        "include_fields": "id, people_type, name",
        **_SUGGEST_PARAMS,
    }


@dataclass
class Suggestion:
    url: str
    title: str
    category: str
    cover_url: str | None = None
    # another indexed title, to tell near-identical rows apart
    alt_title: str = ""


@cache
def _item_classes() -> dict[str, type["Item"]]:
    from catalog.models import Item

    return {cls.__name__: cls for cls in Item.__subclasses__()}


def _cover_url(name: str) -> str | None:
    from catalog.models import Item

    if not name or name == MISSING_COVER:
        return None
    url = cast(ImageField, Item._meta.get_field("cover")).storage.url(name)
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{settings.SITE_INFO['site_url']}{url}"


def _links(pks: list[int]) -> dict[int, tuple[str, str | None]]:
    """``{pk: (uuid, cover url)}`` for the items still worth linking to.

    ``uid`` and ``cover`` are columns of the base table, so this stays a
    single flat query: no polymorphic descent, no related objects, and none
    of the JSON columns. Items that went away or were merged since they were
    indexed are simply absent, and their rows get dropped.
    """
    from catalog.models import Item

    if not pks:
        return {}
    rows = Item.objects.filter(
        pk__in=pks, is_deleted=False, merged_to_item_id__isnull=True
    ).values_list("pk", "uid", "cover")
    return {
        pk: (b62_encode(cast(UUID, uid).int).zfill(22), _cover_url(cover or ""))
        for pk, uid, cover in rows
    }


def _titles(query: str, titles: Sequence[str]) -> tuple[str, str]:
    """The title to show, plus another one as a hint.

    The index stores each localized title without its language, so showing
    the title the query matched beats guessing which one fits the viewer.
    """
    texts = [t for t in titles if t]
    if not texts:
        return "", ""
    q = query.lower()
    title = next((t for t in texts if q in t.lower()), texts[0])
    return title, next((t for t in texts if t != title), "")


def _valid_query(keywords: str) -> bool:
    if len(keywords) == 1:
        return bool(_CJK.search(keywords))
    return SUGGEST_MIN_LENGTH <= len(keywords) <= SUGGEST_MAX_LENGTH


def suggest_items(
    keywords: str,
    categories: "Sequence[ItemCategory] | None" = None,
    exclude_categories: "Sequence[ItemCategory] | None" = None,
) -> list[Suggestion]:
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
    r = CatalogIndex.instance().search(q)
    if r.error:
        return []
    docs = [hit["document"] for hit in r.response.get("hits", [])]
    classes = _item_classes()
    links = _links([int(doc["id"]) for doc in docs])
    suggestions = []
    for doc in docs:
        cls = classes.get(doc.get("item_class", ""))
        link = links.get(int(doc["id"]))
        if cls is None or link is None:
            continue
        uuid, cover_url = link
        title, alt_title = _titles(q.q, doc.get("title") or [])
        suggestions.append(
            Suggestion(
                url=f"/{cls.url_path}/{uuid}",
                title=title,
                alt_title=alt_title,
                category=str(cls.category.label),
                cover_url=cover_url,
            )
        )
    return suggestions


def suggest_people(keywords: str) -> list[Suggestion]:
    from catalog.models import People, PeopleType

    if not _valid_query(keywords):
        return []
    q = PeopleSuggestParser(keywords, page_size=SUGGEST_LIMIT)
    if not q:
        return []
    r = PeopleIndex.instance().search(q)
    if r.error:
        return []
    docs = [hit["document"] for hit in r.response.get("hits", [])]
    links = _links([int(doc["id"]) for doc in docs])
    suggestions = []
    for doc in docs:
        link = links.get(int(doc["id"]))
        if link is None:
            continue
        uuid, cover_url = link
        people_type = doc.get("people_type")
        path = (
            People.url_path_organization
            if people_type == PeopleType.ORGANIZATION
            else People.url_path_person
        )
        title, alt_title = _titles(q.q, doc.get("name") or [])
        suggestions.append(
            Suggestion(
                url=f"/{path}/{uuid}",
                title=title,
                alt_title=alt_title,
                category=str(PeopleType(people_type).label)
                if people_type in PeopleType.values
                else "",
                cover_url=cover_url,
            )
        )
    return suggestions
