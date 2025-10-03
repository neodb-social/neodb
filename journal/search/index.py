import re
from datetime import datetime
from functools import cached_property, reduce
from typing import TYPE_CHECKING, Iterable

from dateutil.relativedelta import relativedelta
from django.db.models import QuerySet

from catalog.models import Item, item_categories
from common.models import int_, uniq
from common.search import Index, QueryParser, SearchResult
from takahe.models import Post
from takahe.utils import Takahe
from users.models.apidentity import APIdentity

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


class JournalQueryParser(QueryParser):
    fields = ["status", "rating", "tag", "category", "type", "date", "sort"]
    status_values = {"wishlist", "progress", "complete", "dropped"}
    type_values = {"shelfmember", "rating", "comment", "review", "collection", "note"}
    sort_values = {"date": "created:desc", "rating": "rating:desc"}
    default_search_params = {
        "query_by": "content, item_title, tag",
        "per_page": 20,
        "highlight_fields": "",
        "include_fields": "post_id, piece_id, item_id, owner_id, piece_class",
    }

    def __init__(self, query: str, page: int = 1, page_size: int = 0):
        from journal.models import Tag

        super().__init__(query, page, page_size)

        v = list(
            set(self.parsed_fields.get("sort", "").split(",")) & self.sort_values.keys()
        )
        if v:
            self.sort_by = [self.sort_values[v[0]]]

        v = list(
            set(self.parsed_fields.get("status", "").split(",")) & self.status_values
        )
        if v:
            self.filter_by["shelf_type"] = v

        v = list(
            set(
                self.parsed_fields.get("type", "")
                .replace("mark", "shelfmember")
                .split(",")
            )
            & self.type_values
        )
        if v:
            self.filter_by["piece_class"] = v
        # else:
        #     # hide collection by default unless specified
        #     self.filter_by["piece_class"] = ["!collection"]

        v = [
            Tag.deep_cleanup_title(i, replacement="_")
            for i in set(self.parsed_fields.get("tag", "").split(","))
            if i
        ]
        if v:
            self.filter_by["tag"] = v
            self.query_by = ["content", "item_title"]

        v = self.parsed_fields.get("rating", "").split("..")
        if len(v) == 2:
            v = list(map(int_, v))
            if all([i >= 0 and i <= 10 for i in v]):
                self.filter_by["rating"] = ["..".join(map(str, v))]
        elif len(v) == 1:
            v = int_(v[0], -1)
            if v >= 0 and v <= 10:
                self.filter_by["rating"] = [v]
        # v = self.filters.get("category", "").split(",")

        v = self.parsed_fields.get("date", "").split("..")
        if len(v) == 2:
            start = self.start_date_to_int(v[0])
            end = self.end_date_to_int(v[1])
        elif len(v) == 1:
            start, end = self.date_to_int_range(v[0])
        else:
            start, end = 0, 0
        if start and end:
            self.filter_by["created"] = [f"{start}..{end}"]

        v = [i for i in set(self.parsed_fields.get("category", "").split(",")) if i]
        if v:
            cats = {
                c.value: [ic.__name__ for ic in cl]
                for c, cl in item_categories().items()
            }
            v = list(set(v) & cats.keys())
            v = reduce(lambda a, b: a + b, [cats[i] for i in v], [])
            self.filter_by["item_class"] = v

    def start_date_to_int(self, date: str) -> int:
        try:
            if re.match(r"\d{4}-\d{1,2}-\d{1,2}", date):
                d = datetime.strptime(date, "%Y-%m-%d")
            elif re.match(r"\d{4}-\d{1,2}", date):
                d = datetime.strptime(date, "%Y-%m")
            elif re.match(r"\d{4}", date):
                d = datetime.strptime(date, "%Y")
            else:
                return 0
            return int(d.timestamp())
        except ValueError:
            return 0

    def end_date_to_int(self, date: str) -> int:
        try:
            if re.match(r"\d{4}-\d{1,2}-\d{1,2}", date):
                d = datetime.strptime(date, "%Y-%m-%d") + relativedelta(days=1)
            elif re.match(r"\d{4}-\d{1,2}", date):
                d = datetime.strptime(date, "%Y-%m") + relativedelta(months=1)
            elif re.match(r"\d{4}", date):
                d = datetime.strptime(date, "%Y") + relativedelta(years=1)
            else:
                return 0
            return int(d.timestamp()) - 1
        except ValueError:
            return 0

    def date_to_int_range(self, date: str) -> tuple[int, int]:
        try:
            if re.match(r"\d{4}-\d{1,2}-\d{1,2}", date):
                start = datetime.strptime(date, "%Y-%m-%d")
                end = start + relativedelta(days=1)
            elif re.match(r"\d{4}-\d{1,2}", date):
                start = datetime.strptime(date, "%Y-%m")
                end = start + relativedelta(months=1)
            elif re.match(r"\d{4}", date):
                start = datetime.strptime(date, "%Y")
                end = start + relativedelta(years=1)
            else:
                return 0, 0
            return int(start.timestamp()), int(end.timestamp()) - 1
        except ValueError:
            return 0, 0

    def filter_by_owner(self, owner: APIdentity):
        self.filter("owner_id", owner.pk)

    def filter_by_viewer(self, viewer: APIdentity):
        self.filter("visibility", 0)
        self.exclude("owner_id", viewer.ignoring)
        # TODO support non-public posts

    def filter_by_owner_viewer(self, owner: APIdentity, viewer: APIdentity | None):
        self.filter("owner_id", owner.pk)
        if not viewer:
            self.filter("visibility", 0)
        elif viewer == owner:
            pass
        elif viewer.is_following(owner):
            self.filter("visibility", [0, 1])
        else:
            self.filter("visibility", 0)


class JournalSearchResult(SearchResult):
    @cached_property
    def items(self):
        if not self:
            return []
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
        return Item.get_final_items(Item.get_by_ids(ids))

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
        select = {f"id_{i}": f"id={i}" for i in ids}
        order = [f"-id_{i}" for i in ids]
        ps = Piece.objects.filter(pk__in=ids).extra(select=select, order_by=order)
        return ps

    @cached_property
    def posts(self) -> QuerySet[Post]:
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
        tbl = Post._meta.db_table
        select = {f"id_{i}": f"{tbl}.id={i}" for i in ids}
        order = [f"-id_{i}" for i in ids]
        ps = (
            Post.objects.filter(pk__in=ids)
            .extra(select=select, order_by=order)
            .exclude(state__in=["deleted", "deleted_fanned_out"])
        )
        return ps

    @property
    def facet_by_item_class(self):
        return self.get_facet("item_class")

    @property
    def facet_by_piece_class(self):
        return self.get_facet("piece_class")

    def __iter__(self):  # type: ignore
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
                # "store": False,
            },
            {
                "name": "content",
                "type": "string[]",
                "locale": "zh",
                "optional": True,
                # "store": False,
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
    search_result_class = JournalSearchResult

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
            # enable this in future when we support search other users
            # doc["viewer_id"] = list(
            #     piece.latest_post.interactions.values_list("identity_id", flat=True)
            # )
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
                "visibility": Takahe.visibility_t2n(post.visibility),
                "owner_id": post.author_id,
                # enable this in future when we support search other users
                # "viewer_id": list(
                #     post.interactions.values_list("identity_id", flat=True)
                # ),
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

    def replace_posts(self, posts: Iterable[Post]):
        docs = self.posts_to_docs(posts)
        self.replace_docs(docs)

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
        query,
    ) -> JournalSearchResult:
        r = super().search(query)
        return r  # type:ignore
