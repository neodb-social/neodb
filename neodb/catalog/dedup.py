"""Find live items that share an external id, and merge the pairs a person approved.

Each live item claims the id of each of its resources and every entry of
their ``other_lookup_ids``. Two items of one model that claim the same id are
a candidate pair. ``report`` writes the pairs to a CSV with a tier and an empty
``approve`` column; ``merge`` merges the rows a person marked, and logs what it
moved so ``undo`` can put it back. Nothing here merges on its own.
"""

import csv
import json
import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import IO, Iterable, Iterator

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.signing import b62_encode
from django.db import connection, transaction
from django.utils import timezone

from catalog.models import (
    Edition,
    ExternalResource,
    IdType,
    Item,
    ItemCredit,
    People,
    TVSeason,
    TVShow,
)

logger = logging.getLogger(__name__)

# ids that tie items loosely: shared by many releases, or edited by anyone
WEAK_ID_TYPES = {IdType.ISRC, IdType.WikiData, IdType.ASIN}
BOOK_ALBUM_ID_TYPES = {IdType.ISBN, IdType.ISBN10, IdType.GTIN}
MAX_GROUP_SIZE = 6
TITLE_SIMILARITY = 0.85

# how an item claims an id, best first
OWN, CITE, PEER = 1, 2, 3
_HOW_LABEL = {OWN: "own", CITE: "cite", PEER: "peer"}

CSV_COLUMNS = [
    "tier",
    "reason",
    "source_id",
    "target_id",
    "source_title",
    "target_title",
    "source_year",
    "target_year",
    "source_marks",
    "target_marks",
    "both_marked",
    "evidence",
    "source_url",
    "target_url",
    "source_resources",
    "target_resources",
    "approve",
]
APPROVE_VALUES = {"y", "yes", "swap", "recast"}

_CLAIMS_SQL = [
    "DROP TABLE IF EXISTS dd_res, dd_claim, dd_c, dd_grp, dd_ev",
    """
    CREATE TEMP TABLE dd_res AS
    SELECT r.item_id, i.polymorphic_ctype_id AS ct, r.id_type, r.id_value,
           r.other_lookup_ids
    FROM catalog_externalresource r
    JOIN catalog_item i ON i.id = r.item_id
    WHERE NOT i.is_deleted AND i.merged_to_item_id IS NULL
    """,
    "CREATE INDEX ON dd_res (item_id)",
    """
    CREATE TEMP TABLE dd_claim AS
    SELECT item_id, ct, id_type, id_value, 1 AS how
    FROM dd_res WHERE id_type <> 'fedi'
    UNION ALL
    SELECT r.item_id, r.ct, e.key, e.value,
           CASE WHEN r.id_type = 'fedi' THEN 3 ELSE 2 END
    FROM dd_res r, jsonb_each_text(r.other_lookup_ids) e
    WHERE jsonb_typeof(r.other_lookup_ids) = 'object' AND e.value <> ''
    """,
    # an id cited only by a peer counts only for an item that is nothing
    # but the peer copy: peer mappings can be wrong
    """
    DELETE FROM dd_claim c WHERE how = 3 AND EXISTS (
        SELECT 1 FROM dd_res r WHERE r.item_id = c.item_id AND r.id_type <> 'fedi'
    )
    """,
    """
    CREATE TEMP TABLE dd_c AS
    SELECT item_id, ct, id_type, id_value, min(how) AS how
    FROM dd_claim GROUP BY item_id, ct, id_type, id_value
    """,
    "DROP TABLE dd_claim",
    "CREATE INDEX ON dd_c (id_type, id_value)",
    """
    CREATE TEMP TABLE dd_grp AS
    SELECT id_type, id_value FROM dd_c GROUP BY id_type, id_value
    HAVING count(*) BETWEEN 2 AND %(max_group)s
    """,
    """
    CREATE TEMP TABLE dd_ev AS
    SELECT a.item_id AS a_id, b.item_id AS b_id, a.ct AS a_ct, b.ct AS b_ct,
           a.id_type, a.id_value, a.how AS a_how, b.how AS b_how
    FROM dd_grp g
    JOIN dd_c a ON a.id_type = g.id_type AND a.id_value = g.id_value
    JOIN dd_c b ON b.id_type = g.id_type AND b.id_value = g.id_value
        AND b.item_id > a.item_id
    """,
]


@dataclass
class ItemInfo:
    pk: int
    ct: int
    titles: list[str]
    year: int | None
    book_format: str | None
    protected: bool
    url: str
    marks: int = 0
    resources: list[tuple[str, str, str]] = field(default_factory=list)
    """(id_type, id_value, url) of each resource"""

    @property
    def own_ids(self) -> dict[str, set[str]]:
        d = defaultdict(set)
        for t, v, _ in self.resources:
            if t != IdType.Fediverse:
                d[t].add(v)
        return d

    @property
    def peer_only(self) -> bool:
        return all(t == IdType.Fediverse for t, _, _ in self.resources)

    @property
    def title(self) -> str:
        return self.titles[0] if self.titles else ""


@dataclass
class Evidence:
    id_type: str
    id_value: str
    a_how: int
    b_how: int

    def __str__(self):
        return (
            f"{self.id_type}:{self.id_value}"
            f"({_HOW_LABEL[self.a_how]}/{_HOW_LABEL[self.b_how]})"
        )


def _normalize_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    return re.sub(r"[\W_]+", "", s)


def titles_similar(a: Iterable[str], b: Iterable[str]) -> bool:
    na = {_normalize_title(t) for t in a if t}
    nb = {_normalize_title(t) for t in b if t}
    na.discard("")
    nb.discard("")
    for x in na:
        for y in nb:
            if x == y:
                return True
            if min(len(x), len(y)) >= 4 and (x in y or y in x):
                return True
            if SequenceMatcher(None, x, y).ratio() >= TITLE_SIMILARITY:
                return True
    return False


def _year(*values) -> int | None:
    for v in values:
        if v is None:
            continue
        m = re.match(r"\s*(\d{4})", str(v))
        if m:
            return int(m[1])
    return None


def book_format(fmt: str | None, binding: str | None) -> str | None:
    """Binding kind of an edition: hard / soft / ebook / audio, or None."""
    if fmt in ("hardcover",):
        return "hard"
    if fmt in ("paperback",):
        return "soft"
    if fmt in ("ebook", "web"):
        return "ebook"
    if fmt in ("audiobook",):
        return "audio"
    b = (binding or "").casefold()
    if not b:
        return None
    if "hard" in b or "精装" in b or "精裝" in b:
        return "hard"
    if "audio" in b or "有声" in b or "有聲" in b:
        return "audio"
    if "ebook" in b or "kindle" in b or "电子" in b or "電子" in b:
        return "ebook"
    if "paper" in b or "soft" in b or "平装" in b or "平裝" in b:
        return "soft"
    return None


def _ct_models() -> dict[int, type[Item]]:
    d = {}
    for ct in ContentType.objects.filter(app_label="catalog"):
        m = ct.model_class()
        if m and issubclass(m, Item):
            d[ct.pk] = m
    return d


def _chunks(seq: list, n: int) -> Iterator[list]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def load_items(pks: list[int], models: dict[int, type[Item]]) -> dict[int, ItemInfo]:
    infos: dict[int, ItemInfo] = {}
    with connection.cursor() as cursor:
        for chunk in _chunks(pks, 2000):
            cursor.execute(
                """
                SELECT id, polymorphic_ctype_id, title, metadata->'localized_title',
                       metadata->>'pub_year', metadata->>'release_date',
                       metadata->>'opening_date', metadata->>'year',
                       metadata->>'format', metadata->>'binding',
                       coalesce(is_protected, false), uid
                FROM catalog_item WHERE id = ANY(%s)
                """,
                [chunk],
            )
            for row in cursor.fetchall():
                pk, ct, title, lt, py, rd, od, yr, fmt, binding, prot, uid = row
                if isinstance(lt, str):  # Django loads jsonb as text
                    lt = json.loads(lt)
                titles = [
                    t["text"]
                    for t in (lt if isinstance(lt, list) else [])
                    if isinstance(t, dict) and t.get("text")
                ]
                if title and title not in titles:
                    titles.append(title)
                model = models.get(ct)
                path = model.url_path if model else "item"
                infos[pk] = ItemInfo(
                    pk=pk,
                    ct=ct,
                    titles=titles,
                    year=_year(py, rd, od, yr),
                    book_format=book_format(fmt, binding) if model is Edition else None,
                    protected=prot,
                    url=f"/{path}/{b62_encode(uid.int).zfill(22)}",
                )
            cursor.execute(
                "SELECT item_id, id_type, id_value, url FROM catalog_externalresource"
                " WHERE item_id = ANY(%s) ORDER BY id",
                [chunk],
            )
            for pk, t, v, u in cursor.fetchall():
                infos[pk].resources.append((t, v, u))
            cursor.execute(
                "SELECT item_id, count(*) FROM journal_shelfmember"
                " WHERE item_id = ANY(%s) GROUP BY item_id",
                [chunk],
            )
            for pk, n in cursor.fetchall():
                infos[pk].marks = n
    return infos


def _both_marked(pairs: list[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """Number of users who marked both items of each pair."""
    result = {}
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS dd_pairs")
        cursor.execute("CREATE TEMP TABLE dd_pairs (a int, b int)")
        for chunk in _chunks(pairs, 5000):
            cursor.execute(
                "INSERT INTO dd_pairs SELECT * FROM unnest(%s::int[], %s::int[])",
                [[a for a, _ in chunk], [b for _, b in chunk]],
            )
        cursor.execute(
            """
            SELECT p.a, p.b, count(DISTINCT x.owner_id)
            FROM dd_pairs p
            JOIN journal_shelfmember x ON x.item_id = p.a
            JOIN journal_shelfmember y ON y.item_id = p.b AND y.owner_id = x.owner_id
            GROUP BY p.a, p.b
            """
        )
        for a, b, n in cursor.fetchall():
            result[(a, b)] = n
        cursor.execute("DROP TABLE dd_pairs")
    return result


def _clusters(pairs: Iterable[tuple[int, int]]) -> dict[int, int]:
    """Size of the connected component each item is in."""
    parent: dict[int, int] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    sizes: dict[int, int] = defaultdict(int)
    for x in parent:
        sizes[find(x)] += 1
    return {x: sizes[find(x)] for x in parent}


@dataclass
class Candidate:
    tier: str
    reason: str
    source: ItemInfo
    target: ItemInfo
    both_marked: int
    evidence: list[Evidence]

    def row(self, site_url: str) -> dict:
        def res(i: ItemInfo) -> str:
            return " ".join(u for _, _, u in i.resources)

        return {
            "tier": self.tier,
            "reason": self.reason,
            "source_id": self.source.pk,
            "target_id": self.target.pk,
            "source_title": self.source.title,
            "target_title": self.target.title,
            "source_year": self.source.year or "",
            "target_year": self.target.year or "",
            "source_marks": self.source.marks,
            "target_marks": self.target.marks,
            "both_marked": self.both_marked,
            "evidence": "; ".join(str(e) for e in self.evidence),
            "source_url": site_url + self.source.url,
            "target_url": site_url + self.target.url,
            "source_resources": res(self.source),
            "target_resources": res(self.target),
            "approve": "",
        }


def _order(a: ItemInfo, b: ItemInfo, evidence: list[Evidence]) -> tuple:
    """(source, target): keep the item that owns the shared id, or has more
    sources; a peer-only item is never kept over one with its own sources."""

    def score(i: ItemInfo, owns: bool):
        return (
            not i.peer_only,
            i.protected,
            owns,
            len([r for r in i.resources if r[0] != IdType.Fediverse]),
            -i.pk,
        )

    a_owns = any(e.a_how == OWN and e.b_how != OWN for e in evidence)
    b_owns = any(e.b_how == OWN and e.a_how != OWN for e in evidence)
    if score(a, a_owns) >= score(b, b_owns):
        return b, a, True
    return a, b, False


def classify(
    a: ItemInfo,
    b: ItemInfo,
    evidence: list[Evidence],
    both_marked: int,
    cluster_size: int,
    models: dict[int, type[Item]],
) -> Candidate | None:
    ma, mb = models.get(a.ct), models.get(b.ct)
    if {ma, mb} == {TVShow, TVSeason}:
        evidence = [e for e in evidence if e.id_type != IdType.IMDB]
        if not evidence:
            return None
    source, target, swapped = _order(a, b, evidence)
    if swapped:
        evidence = [Evidence(e.id_type, e.id_value, e.b_how, e.a_how) for e in evidence]
    hard = [e for e in evidence if e.id_type not in WEAK_ID_TYPES]

    def cand(tier, reason):
        return Candidate(tier, reason, source, target, both_marked, evidence)

    if not hard or People in (ma, mb):
        return cand("X", "weak ids only" if not hard else "people")
    if ma is not mb:
        return cand(
            "C",
            f"models differ: {getattr(ma, '__name__', a.ct)}"
            f" {getattr(mb, '__name__', b.ct)}",
        )

    problems = []
    if not titles_similar(source.titles, target.titles):
        problems.append("titles differ")
    if source.year and target.year:
        tolerance = 0 if ma is Edition else 1
        if abs(source.year - target.year) > tolerance:
            problems.append("years differ")
    if (
        source.book_format
        and target.book_format
        and source.book_format != target.book_format
    ):
        problems.append("formats differ")
    so, to = source.own_ids, target.own_ids
    conflict = [t for t in so if t in to and not (so[t] & to[t])]
    if cluster_size >= 3:
        problems.append(f"cluster of {cluster_size}")

    owner_cites = [e for e in hard if (e.a_how == OWN) != (e.b_how == OWN)]
    both_cite = [
        e
        for e in hard
        if e.id_type in BOOK_ALBUM_ID_TYPES and e.a_how != OWN and e.b_how != OWN
    ]
    tier_a = bool(owner_cites) and not problems and not conflict
    tier_b1 = bool(both_cite) and not [
        p for p in problems if not p.startswith("cluster")
    ]
    if tier_a or tier_b1:
        reason = "one owns the id the other cites" if tier_a else "same isbn/gtin cited"
        if both_marked:
            return cand("B2", f"{reason}; users marked both")
        return cand("A" if tier_a else "B1", reason)
    if conflict:
        problems.append("own ids differ: " + ",".join(sorted(conflict)))
    return cand("B3", "; ".join(problems) or "shared id")


def report(out: IO[str], site_url: str = "", stdout=None) -> dict[str, int]:
    """Write candidate pairs to ``out`` as CSV; return the count per tier."""
    models = _ct_models()
    with connection.cursor() as cursor:
        for sql in _CLAIMS_SQL:
            cursor.execute(sql, {"max_group": MAX_GROUP_SIZE})
        cursor.execute("SELECT count(*) FROM dd_ev")
        row = cursor.fetchone()
        n_ev = row[0] if row else 0
    if stdout:
        stdout.write(f"{n_ev} shared ids between candidate pairs")
    evidence: dict[tuple[int, int], list[Evidence]] = defaultdict(list)
    with connection.chunked_cursor() as cursor:
        cursor.execute(
            "SELECT a_id, b_id, id_type, id_value, a_how, b_how FROM dd_ev"
            " ORDER BY a_id, b_id"
        )
        while rows := cursor.fetchmany(10000):
            for a_id, b_id, t, v, ah, bh in rows:
                evidence[(a_id, b_id)].append(Evidence(t, v, ah, bh))
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS dd_res, dd_c, dd_grp, dd_ev")
    pairs = list(evidence.keys())
    pks = sorted({pk for p in pairs for pk in p})
    infos = load_items(pks, models)
    marked = _both_marked(pairs)
    clusters = _clusters(pairs)
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    counts: dict[str, int] = defaultdict(int)
    for (a_id, b_id), ev in evidence.items():
        a, b = infos.get(a_id), infos.get(b_id)
        if not a or not b:
            continue
        c = classify(a, b, ev, marked.get((a_id, b_id), 0), clusters[a_id], models)
        if c:
            writer.writerow(c.row(site_url))
            counts[c.tier] += 1
    return dict(counts)


def duplicate_primary_ids(limit: int | None = None) -> list[tuple]:
    """Live items of one model sharing a primary lookup id: the cheapest
    duplicate invariant. Rows of (model, id_type, id_value, item ids)."""
    models = _ct_models()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT polymorphic_ctype_id, primary_lookup_id_type,
                   primary_lookup_id_value, array_agg(id ORDER BY id)
            FROM catalog_item
            WHERE NOT is_deleted AND merged_to_item_id IS NULL
              AND primary_lookup_id_type IS NOT NULL
              AND primary_lookup_id_value IS NOT NULL
              AND primary_lookup_id_value <> ''
            GROUP BY 1, 2, 3 HAVING count(*) > 1
            ORDER BY 1, 2, 3
            """
            + (" LIMIT %s" if limit else ""),
            [limit] if limit else [],
        )
        return [
            (getattr(models.get(ct), "__name__", ct), t, v, ids)
            for ct, t, v, ids in cursor.fetchall()
        ]


def item_claims(item: Item) -> dict[tuple[str, str], int]:
    """Ids a live item claims, by the same rules as the report."""
    claims: dict[tuple[str, str], int] = {}
    resources = list(item.external_resources.all())
    peer_only = all(r.id_type == IdType.Fediverse for r in resources)
    for r in resources:
        if r.id_type != IdType.Fediverse:
            claims[(r.id_type, r.id_value)] = OWN
        if r.id_type == IdType.Fediverse and not peer_only:
            continue
        how = PEER if r.id_type == IdType.Fediverse else CITE
        if isinstance(r.other_lookup_ids, dict):
            for t, v in r.other_lookup_ids.items():
                if v:
                    k = (t, str(v))
                    claims[k] = min(claims.get(k, how), how)
    return claims


def _piece_classes() -> list:
    from journal.models.common import Content
    from journal.models.itemlist import ListMember
    from journal.models.shelf import ShelfLogEntry

    return (
        list(Content.__subclasses__())
        + list(ListMember.__subclasses__())
        + [ShelfLogEntry]
    )


def _is_protected(item: Item) -> bool:
    return bool(item.is_protected) or item.verified_creators.exists()


@dataclass
class MergeResult:
    status: str
    """merged, dry-run or a reason for skipping"""
    source_id: int | str | None = None
    target_id: int | str | None = None
    log: dict | None = None


def merge_row(row: dict, commit: bool) -> MergeResult:
    """Check one approved CSV row against the live catalog and merge it."""
    approve = (row.get("approve") or "").strip().lower()
    try:
        source_id, target_id = int(row["source_id"]), int(row["target_id"])
    except KeyError, TypeError, ValueError:
        return MergeResult("bad row")
    if approve == "swap":
        source_id, target_id = target_id, source_id
    source = Item.objects.filter(pk=source_id).first()
    target = Item.objects.filter(pk=target_id).first()
    r = MergeResult("", source_id, target_id)
    if not source or not target:
        r.status = "item missing"
    elif source.is_deleted or source.merged_to_item_id:
        r.status = "source not live"
    elif target.is_deleted or target.merged_to_item_id:
        r.status = "target not live"
    elif _is_protected(source):
        r.status = "source protected"
    elif type(source) is not type(target) and approve != "recast":
        r.status = "models differ"
    elif not {
        k
        for k in item_claims(source).keys() & item_claims(target).keys()
        if k[0] not in WEAK_ID_TYPES
    }:
        r.status = "evidence gone"
    if r.status:
        return r
    assert source and target
    if not commit:
        r.status = "dry-run"
        return r
    with transaction.atomic():
        r.log = _merge(source, target)
    r.status = "merged"
    return r


def _merge(source: Item, target: Item) -> dict:
    from journal.models.utils import update_journal_for_merged_item

    recast_from = None
    if type(source) is not type(target):
        recast_from = source.__class__._meta.label
        source = source.recast_to(target.__class__)
    pieces_before = [
        (cls._meta.label, pk)
        for cls in _piece_classes()
        for pk in cls.objects.filter(item=source).values_list("pk", flat=True)
    ]
    resources = list(source.external_resources.values_list("pk", flat=True))
    credits = list(source.credits.values_list("pk", flat=True))
    repointed = list(
        Item.objects.filter(merged_to_item=source).values_list("pk", flat=True)
    )
    log = {
        "time": timezone.now().isoformat(),
        "source": source.pk,
        "target": target.pk,
        "recast_from": recast_from,
        "target_primary": [
            target.primary_lookup_id_type,
            target.primary_lookup_id_value,
        ],
        "resources": resources,
        "repointed": repointed,
    }
    source.merge_to(target)
    # never deletes: pieces that would clash with the target stay on source
    update_journal_for_merged_item(source.uuid)
    log["pieces"] = [
        [label, pk]
        for label, pk in pieces_before
        if apps.get_model(label).objects.filter(pk=pk, item_id=target.pk).exists()
    ]
    log["credits"] = list(
        ItemCredit.objects.filter(pk__in=credits, item_id=target.pk).values_list(
            "pk", flat=True
        )
    )
    Item.objects.filter(pk__in=repointed).update(merged_to_item=target)
    return log


def merge_csv(
    rows: Iterable[dict], commit: bool, tiers: set[str] | None, log_file: IO[str] | None
) -> Iterator[MergeResult]:
    for row in rows:
        if (row.get("approve") or "").strip().lower() not in APPROVE_VALUES:
            continue
        if tiers and row.get("tier") not in tiers:
            continue
        try:
            r = merge_row(row, commit)
        except Exception as e:
            logger.exception(f"dedup merge failed for {row}")
            r = MergeResult(f"error: {e}", row.get("source_id"), row.get("target_id"))
        if r.log and log_file:
            log_file.write(json.dumps(r.log) + "\n")
            log_file.flush()
        yield r


def undo_entry(entry: dict) -> str:
    """Revert one logged merge; return a status."""
    source = Item.objects.filter(pk=entry["source"]).first()
    target_id = entry["target"]
    if not source:
        return "source missing"
    if source.merged_to_item_id != target_id:
        return "source not merged to target"
    with transaction.atomic():
        source.merge_to(None)
        source.log_action({"!unmerged": [str(target_id), ""]})
        for res in ExternalResource.objects.filter(
            pk__in=entry["resources"], item_id=target_id
        ):
            res.item = source
            res.save(update_fields=["item"])
        ItemCredit.objects.filter(pk__in=entry["credits"], item_id=target_id).update(
            item_id=source.pk
        )
        from journal.models.common import Content
        from journal.models.itemlist import ListMember

        for label, pk in entry["pieces"]:
            p = apps.get_model(label).objects.filter(pk=pk, item_id=target_id).first()
            if p:
                p.item = source
                p.save(update_fields=["item_id"])
                if isinstance(p, (Content, ListMember)):
                    p.update_index()
        Item.objects.filter(
            pk__in=entry["repointed"], merged_to_item_id=target_id
        ).update(merged_to_item=source)
        target = Item.objects.get(pk=target_id)
        t, v = entry["target_primary"]
        if (target.primary_lookup_id_type, target.primary_lookup_id_value) != (t, v):
            target.primary_lookup_id_type = t
            target.primary_lookup_id_value = v
            target.save(
                update_fields=["primary_lookup_id_type", "primary_lookup_id_value"]
            )
        if entry.get("recast_from"):
            source.recast_to(apps.get_model(entry["recast_from"]))
    return "reverted"


def read_csv(f: IO[str]) -> Iterator[dict]:
    yield from csv.DictReader(f)
