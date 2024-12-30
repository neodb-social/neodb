import re
from functools import cached_property, reduce
from typing import TYPE_CHECKING, Iterable

from django.db.models import QuerySet

from catalog.models import Item
from common.models import Index, SearchResult, int_, uniq
from takahe.models import Post
from takahe.utils import Takahe

if TYPE_CHECKING:
    from journal.models import Piece


def _get_item_ids(doc):
    from journal.models import Collection

    if doc.get("piece_class") != ["Collection"]:
        return doc["item_id"]
    return list(
        Collection.objects.filter(id__in=doc["piece_id"]).values_list(
            "catalog_item_id", flat=True
        )
    )


class JournalSearchResult(SearchResult):
    @cached_property
    def items(self):
        if not self:
            return Item.objects.none()
        ids = uniq(
            reduce(
                lambda a, b: a + b,
                [
                    _get_item_ids(hit["document"])
                    for hit in self.response["hits"]
                    if "item_id" in hit["document"]
                ],
                [],
            )
        )
        items = Item.objects.filter(pk__in=ids, is_deleted=False)
        items = [j for j in [i.final_item for i in items] if not j.is_deleted]
        return items

    @cached_property
    def pieces(self):
        from journal.models import Piece

        if not self:
            return Piece.objects.none()
        ids = reduce(
            lambda a, b: a + b,
            [
                hit["document"]["piece_id"]
                for hit in self.response["hits"]
                if "piece_id" in hit["document"]
            ],
            [],
        )
        ps = Piece.objects.filter(pk__in=ids)
        return ps

    @cached_property
    def posts(self):
        if not self:
            return Post.objects.none()
        ids = reduce(
            lambda a, b: a + b,
            [
                hit["document"]["post_id"]
                for hit in self.response["hits"]
                if "post_id" in hit["document"]
            ],
            [],
        )
        ps = Post.objects.filter(pk__in=ids).exclude(
            state__in=["deleted", "deleted_fanned_out"]
        )
        return ps

    @property
    def facet_by_item_class(self):
        return self.get_facet("item_class")

    @property
    def facet_by_piece_class(self):
        return self.get_facet("piece_class")

    def __iter__(self):
        return iter(self.posts)

    def __getitem__(self, key):
        return self.posts[key]

    def __contains__(self, item):
        return item in self.posts


class JournalIndex(Index):
    name = "journal"
    schema = {
        "fields": [
            {
                "name": "post_id",
                "type": "int64[]",
                "sort": False,
                "optional": True,
            },
            {
                "name": "piece_id",
                "type": "int64[]",
                "sort": False,
                "optional": True,
            },
            {
                "name": "piece_class",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },
            {
                "name": "item_id",
                "type": "int64[]",
                "optional": True,
            },
            {
                "name": "item_class",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },
            {
                "name": "item_title",
                "type": "string[]",
                "locale": "zh",
                "optional": True,
            },
            {
                "name": "content",
                "type": "string[]",
                "locale": "zh",
                "optional": True,
            },
            {
                "name": "shelf_type",
                "type": "string",
                "optional": True,
            },
            {
                "name": "rating",
                "type": "int32",
                "range_index": True,
                "optional": True,
            },
            {
                "name": "tag",
                "type": "string[]",
                "locale": "zh",
                "optional": True,
            },
            {
                "name": "created",
                "type": "int64",
            },
            {
                "name": "owner_id",
                "type": "int64",
                "sort": False,
            },
            {
                "name": "visibility",
                "type": "int32",
                "sort": False,
            },
            {
                "name": "viewer_id",
                "type": "int64[]",
                "sort": False,
                "optional": True,
            },
        ]
    }
    default_search_params = {
        "query_by": "content, item_title, tag",
        "sort_by": "created:desc",
        "per_page": 20,
        "highlight_fields": "",
        "include_fields": "post_id, piece_id, item_id, owner_id, piece_class",
        "facet_by": "item_class, piece_class",
    }

    @classmethod
    def piece_to_doc(cls, piece: "Piece") -> dict:
        d = piece.to_indexable_doc()
        if not d:
            return {}
        doc = {
            "id": (
                str(piece.latest_post_id)
                if piece.latest_post_id
                else "p" + str(piece.pk)
            ),
            "piece_id": [piece.pk],
            "piece_class": [piece.__class__.__name__],
            "created": int(piece.created_time.timestamp()),  # type: ignore
            "owner_id": piece.owner_id,
            "visibility": piece.visibility,
        }
        if piece.latest_post:
            # fk is not enforced, so post might be deleted
            doc["post_id"] = [piece.latest_post_id]
            doc["viewer_id"] = list(
                piece.latest_post.interactions.values_list("identity_id", flat=True)
            )
        doc.update(d)
        return doc

    @classmethod
    def pieces_to_docs(cls, pieces: "Iterable[Piece]") -> list[dict]:
        docs = [cls.piece_to_doc(p) for p in pieces]
        return [d for d in docs if d]

    @classmethod
    def post_to_doc(cls, post: Post) -> dict:
        pc = post.piece
        doc = {}
        if pc:
            pc.latest_post = post
            pc.latest_post_id = post.pk
            doc = cls.piece_to_doc(pc)
        if not doc:
            doc = {
                "id": str(post.pk),
                "post_id": [post.pk],
                "piece_class": ["Post"],
                "content": [post.content],
                "created": int(post.created.timestamp()),
                "owner_id": post.author_id,
                "viewer_id": list(
                    post.interactions.values_list("identity_id", flat=True)
                ),
                "visibility": Takahe.visibility_t2n(post.visibility),
            }
        return doc

    @classmethod
    def posts_to_docs(cls, posts: Iterable[Post]) -> list[dict]:
        return [cls.post_to_doc(p) for p in posts]

    def delete_all(self):
        return self.delete_docs("owner_id", ">0")

    def delete_by_owner(self, owner_ids):
        return self.delete_docs("owner_id", owner_ids)

    def delete_by_piece(self, piece_ids):
        return self.delete_docs("piece_id", piece_ids)

    def delete_by_post(self, post_ids):
        return self.delete_docs("post_id", post_ids)

    def replace_pieces(self, pieces: "Iterable[Piece] | QuerySet[Piece]"):
        if isinstance(pieces, QuerySet):
            pids = pieces.values_list("pk", flat=True)
        else:
            pids = [p.pk for p in pieces]
        if not pids:
            return
        self.delete_by_piece(pids)
        self.insert_docs(self.pieces_to_docs(pieces))

    def search(
        self,
        q: str,
        page: int = 1,
        page_size: int = 0,
        query_by: list[str] = [],
        sort_by: str = "",
        filter_by: dict[str, list[str | int]] = {},
        facet_by: list[str] = [],
        result_class=JournalSearchResult,
    ) -> JournalSearchResult:
        r = super().search(
            q=q,
            page=page,
            page_size=page_size,
            query_by=query_by,
            sort_by=sort_by,
            filter_by=filter_by,
            facet_by=facet_by,
            result_class=result_class,
        )
        return r


class QueryParser:
    fields = ["status", "rating", "tag", "category", "type"]

    @classmethod
    def re(cls):
        return re.compile(
            r"\b(?P<field>" + "|".join(cls.fields) + r"):(?P<value>[^ ]+)"
        )

    def __init__(self, query: str):
        self.query = str(query) if query else ""
        r = self.re()
        self.filters = {
            m.group("field").strip().lower(): m.group("value").strip().lower()
            for m in r.finditer(query)
        }
        self.q = r.sub("", query).strip()
        self.filter_by = {}
        self.query_by = ["content", "item_title", "tag"]

        v = list(
            set(self.filters.get("status", "").split(","))
            & {"wishlist", "progress", "complete"}
        )
        if v:
            self.filter_by["shelf_type"] = v

        v = list(
            set(self.filters.get("type", "").replace("mark", "shelfmember").split(","))
            & {"shelfmember", "rating", "comment", "review", "collection", "note"}
        )
        if v:
            self.filter_by["piece_class"] = v
        # else:
        #     # hide collection by default unless specified
        #     self.filter_by["piece_class"] = ["!collection"]

        v = [i for i in set(self.filters.get("tag", "").split(",")) if i]
        if v:
            self.filter_by["tag"] = v
            self.query_by.remove("tag")

        v = self.filters.get("rating", "").split("..")
        if len(v) == 2:
            v = map(int_, v)
            self.filter_by["rating"] = ["..".join(map(str, v))]
        elif len(v) == 1:
            v = int_(v[0])
            if v:
                self.filter_by["rating"] = [v]
