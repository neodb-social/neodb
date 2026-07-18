from argparse import RawTextHelpFormatter
from datetime import timedelta
from itertools import batched

from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from tqdm import tqdm

from catalog.models import Item
from common.management.base import SiteCommand
from journal.exporters.ndjson import NdjsonExporter
from journal.models import (
    Article,
    Collection,
    Comment,
    Content,
    Note,
    Piece,
    Review,
    ShelfMember,
    update_journal_for_merged_item,
)
from journal.models.itemlist import ListMember
from journal.search import JournalIndex, JournalQueryParser
from takahe.models import Post
from users.models import APIdentity, User

_CONFIRM = "confirm deleting collection? [Y/N] "

_HELP_TEXT = """
intergrity:     check and fix remaining journal for merged and deleted items
purge:          delete invalid data (visibility=99)
export:         run export task
search:         search docs in index
idx-info:       show index information
idx-init:       check and create index if not exists
idx-destroy:    delete index
idx-alt:        update index schema
idx-delete:     delete docs in index
idx-reindex:    reindex docs
idx-catchup:    update index for journal items edited in last X hours (use --hour)
idx-sync:       add missing docs and delete stale docs for each local identity,
                purge docs of deactivated identities (use --dry-run to preview)
"""

_DELETED_POST_STATES = ["deleted", "deleted_fanned_out"]

# Piece classes whose to_indexable_doc() may produce a doc of its own; the
# others (Rating, Tag, TagMember, Shelf, CollectionMember, FeaturedCollection,
# Debris) always return {} and are never indexed individually.
_INDEXABLE_PIECE_CLASSES: list[type[Piece]] = [
    ShelfMember,
    Comment,
    Review,
    Collection,
    Note,
    Article,
]

_SYNC_DELETE_CHUNK = 200


class Command(SiteCommand):
    help = "journal app utilities"

    def create_parser(self, *args, **kwargs):
        parser = super(Command, self).create_parser(*args, **kwargs)
        parser.formatter_class = RawTextHelpFormatter
        return parser

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=[
                "integrity",
                "purge",
                "export",
                "idx-info",
                "idx-init",
                "idx-alt",
                "idx-destroy",
                "idx-reindex",
                "idx-delete",
                "search",
                "idx-catchup",
                "idx-sync",
            ],
            help=_HELP_TEXT,
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
        )
        parser.add_argument(
            "--owner",
            action="append",
        )
        parser.add_argument(
            "--query",
        )
        parser.add_argument(
            "--batch-size",
            default=1000,
        )
        parser.add_argument(
            "--item-class",
            action="append",
        )
        parser.add_argument(
            "--piece-class",
            action="append",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
        )
        parser.add_argument(
            "--fast",
            action="store_true",
            help="skip some inactive users and rare cases to speed up index",
        )
        parser.add_argument(
            "--remote",
            action="store_true",
            help="reindex remote pieces only, does not work with --owner",
        )
        parser.add_argument(
            "--hour",
            type=int,
            help="Number of hours to look back for edited items (used with idx-catchup)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what idx-sync would change without writing to index",
        )

    def integrity(self):
        self.stdout.write("Checking deleted items with remaining journals...")
        for i in Item.objects.filter(is_deleted=True):
            if i.journal_exists():
                self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")

        self.stdout.write("Checking merged items with remaining journals...")
        for i in Item.objects.filter(merged_to_item__isnull=False):
            if i.journal_exists():
                self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
                if self.fix:
                    update_journal_for_merged_item(i.url)

    def export(self, owner_ids):
        users = User.objects.filter(identity__in=owner_ids)
        for user in users:
            task = NdjsonExporter.create(user=user)
            self.stdout.write(f"exporting for {user} (task {task.pk})...")
            ok = task._run()
            if ok:
                self.stdout.write(f"complete {task.metadata['file']}")
            else:
                self.stdout.write("failed")
            task.delete()

    def idx_catchup(self, hours):
        if hours is None:
            self.stdout.write(self.style.ERROR("--hour parameter is required"))
            return
        cutoff_time = timezone.now() - timedelta(hours=hours)
        model_classes = [ShelfMember, Review, Comment, Collection, Note]
        for model_cls in model_classes:
            items = model_cls.objects.filter(edited_time__gte=cutoff_time).order_by(
                "pk"
            )
            count = items.count()
            self.stdout.write(
                f"{count} {model_cls.__name__}(s) edited since {cutoff_time}"
            )
            with tqdm(total=count, desc=f"Updating {model_cls.__name__}") as pbar:
                for item in items.iterator():
                    try:
                        item.update_index()
                        pbar.set_description(f"Updated {model_cls.__name__}: {item.pk}")
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(
                                f"Error updating index for {model_cls.__name__} {item.pk}: {e}"
                            )
                        )
                    pbar.update(1)

    def expected_index_docs(self, identity_id: int) -> dict[str, tuple[str, int]]:
        """Doc ids an active local identity should have in the index.

        Returns a map of doc id -> ("post" | "piece", pk), mirroring how
        JournalIndex.post_to_doc() / piece_to_doc() assign doc ids.
        """
        expected: dict[str, tuple[str, int]] = {}
        live_post_ids = set(
            Post.objects.filter(local=True, author_id=identity_id)
            .exclude(state__in=_DELETED_POST_STATES)
            .values_list("pk", flat=True)
        )
        for post_id in live_post_ids:
            expected[str(post_id)] = ("post", post_id)
        piece_ids: set[int] = set()
        # piece_id -> (piece_post_pk, post_id) of the latest linked post
        latest_pps: dict[int, tuple[int, int]] = {}
        for cls in _INDEXABLE_PIECE_CLASSES:
            pieces = cls.objects.filter(owner_id=identity_id, local=True)
            if cls is Comment:
                # comment with a sibling mark is indexed within its ShelfMember doc
                pieces = pieces.exclude(
                    item_id__in=ShelfMember.objects.filter(owner_id=identity_id).values(
                        "item_id"
                    )
                )
            # left join keeps pieces without any post
            rows = pieces.values_list(
                "pk", "post_relations__pk", "post_relations__post_id"
            )
            for piece_id, pp_pk, post_id in rows:
                piece_ids.add(piece_id)
                if pp_pk is not None and (
                    piece_id not in latest_pps or pp_pk > latest_pps[piece_id][0]
                ):
                    latest_pps[piece_id] = (pp_pk, post_id)
        for piece_id in piece_ids:
            post_id = latest_pps[piece_id][1] if piece_id in latest_pps else None
            if post_id is None:
                expected["p" + str(piece_id)] = ("piece", piece_id)
            elif post_id not in live_post_ids:
                # piece_to_doc() keys the doc by the latest linked post id
                # even if that post is gone from db
                expected[str(post_id)] = ("piece", piece_id)
            # otherwise the piece is covered by the doc of its live post
        return expected

    def sync_identity_index(
        self, index: JournalIndex, identity_id: int
    ) -> tuple[int, int] | None:
        """Add missing docs and delete stale docs for one active identity.

        Docs already in the index are left as is (no deep comparison).
        Returns (added, deleted), or None on index error.
        """
        expected = self.expected_index_docs(identity_id)
        indexed = index.get_doc_ids_by_owner(identity_id)
        if indexed is None:
            return None
        extra = indexed - expected.keys()
        missing = expected.keys() - indexed
        if self.dry_run:
            return len(missing), len(extra)
        deleted = 0
        for chunk in batched(extra, _SYNC_DELETE_CHUNK):
            deleted += index.delete_docs("id", chunk)
        added = 0
        post_ids = [expected[i][1] for i in missing if expected[i][0] == "post"]
        for chunk in batched(post_ids, self.batch_size):
            posts = Post.objects.filter(pk__in=chunk)
            added += index.replace_docs(index.posts_to_docs(posts))
        piece_ids = [expected[i][1] for i in missing if expected[i][0] == "piece"]
        for chunk in batched(piece_ids, self.batch_size):
            pieces = Piece.objects.filter(pk__in=chunk)
            added += index.replace_docs(index.pieces_to_docs(pieces))
        return added, deleted

    def idx_sync(self, index: JournalIndex, owners: list[int]):
        identities = APIdentity.objects.filter(local=True)
        if owners:
            identities = identities.filter(pk__in=owners)
        # mirror APIdentity.is_active
        active_q = Q(user__isnull=False, user__is_active=True) | Q(
            user__isnull=True, deleted__isnull=True
        )
        active_ids = list(
            identities.filter(active_q).order_by("pk").values_list("pk", flat=True)
        )
        inactive_ids = list(
            identities.exclude(active_q).order_by("pk").values_list("pk", flat=True)
        )
        added = deleted = errors = 0
        for identity_id in tqdm(active_ids, desc="Syncing active identities"):
            r = self.sync_identity_index(index, identity_id)
            if r is None:
                errors += 1
                continue
            a, d = r
            added += a
            deleted += d
            if self.verbose and (a or d):
                self.stdout.write(f"identity {identity_id}: +{a} -{d}")
        purged = 0
        if self.dry_run:
            for identity_id in tqdm(inactive_ids, desc="Checking deactivated"):
                ids = index.get_doc_ids_by_owner(identity_id)
                if ids is None:
                    errors += 1
                else:
                    purged += len(ids)
        else:
            for chunk in batched(inactive_ids, 100):
                purged += index.delete_by_owner(chunk)
        w = "would be " if self.dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"idx-sync complete: {len(active_ids)} active identities, "
                f"{added} docs {w}added, {deleted} docs {w}deleted; "
                f"{len(inactive_ids)} deactivated identities, {purged} docs {w}purged."
            )
        )
        if errors:
            self.stdout.write(
                self.style.WARNING(f"{errors} identities skipped due to index errors.")
            )

    def handle(
        self,
        action,
        yes,
        query,
        owner,
        piece_class,
        item_class,
        verbose,
        fix,
        batch_size,
        fast,
        remote,
        *args,
        **kwargs,
    ):
        self.verbose = verbose
        self.fix = fix
        self.batch_size = int(batch_size)
        self.dry_run = kwargs.get("dry_run", False)
        index = JournalIndex.instance()

        if owner and not remote:
            owners = list(
                APIdentity.objects.filter(username__in=owner, local=True).values_list(
                    "id", flat=True
                )
            )
        else:
            if owner:
                self.stdout.write(
                    self.style.WARNING("--owner is ignored when --remote is set")
                )
            owners = []

        match action:
            case "integrity":
                self.integrity()
                self.stdout.write(self.style.SUCCESS("Done."))

            case "purge":
                for pcls in [Content, ListMember]:
                    for cls in pcls.__subclasses__():
                        self.stdout.write(f"Cleaning up {cls}...")
                        cls.objects.filter(visibility=99).delete()
                self.stdout.write(self.style.SUCCESS("Done."))

            case "export":
                self.export(owners)

            case "idx-destroy":
                if yes or input(_CONFIRM).upper().startswith("Y"):
                    index.delete_collection()
                    self.stdout.write(self.style.SUCCESS("deleted."))

            case "idx-alt":
                # index.update_schema()
                self.stdout.write(self.style.SUCCESS("not implemented."))

            case "idx-init":
                index.initialize_collection()
                self.stdout.write(self.style.SUCCESS("initialized."))

            case "idx-info":
                try:
                    r = index.check()
                    self.stdout.write(str(r))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(str(e)))

            case "idx-delete":
                if owners:
                    c = index.delete_by_owner(owners)
                else:
                    c = index.delete_all()
                self.stdout.write(self.style.SUCCESS(f"deleted {c} documents."))

            case "idx-reindex":
                if fast and not owners:
                    q = Q(social_accounts__type="mastodon.mastodonaccount") | Q(
                        social_accounts__last_reachable__gt=timezone.now()
                        - timedelta(days=365)
                    )
                    owners = list(
                        User.objects.filter(is_active=True)
                        .filter(q)
                        .values_list("identity", flat=True)
                    )
                if not remote:
                    # index all posts first
                    posts = Post.objects.filter(local=True).exclude(
                        state__in=["deleted", "deleted_fanned_out"]
                    )
                    if owners:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"indexing for {len(owners)} local users."
                            )
                        )
                        posts = posts.filter(author_id__in=owners)
                    c = 0
                    pg = Paginator(posts.order_by("id"), self.batch_size)
                    for p in tqdm(pg.page_range):
                        docs = index.posts_to_docs(pg.get_page(p).object_list)
                        c += len(docs)
                        index.replace_docs(docs)
                    self.stdout.write(self.style.SUCCESS(f"indexed {c} local posts."))
                # index remaining pieces without posts
                for cls in (
                    [
                        ShelfMember,
                        Review,
                        Collection,
                    ]
                    if fast
                    else [Piece]
                ):
                    pieces = cls.objects.filter(local=not remote)
                    if owners:
                        pieces = pieces.filter(owner_id__in=owners)
                    c = 0
                    pg = Paginator(pieces.order_by("id"), self.batch_size)
                    for p in tqdm(pg.page_range):
                        pieces = pg.get_page(p).object_list
                        if not remote:
                            pieces = [p for p in pieces if p.latest_post is None]
                        docs = index.pieces_to_docs(pieces)
                        c += len(docs)
                        index.replace_docs(docs)
                    self.stdout.write(
                        self.style.SUCCESS(f"indexed {c} {cls.__name__}.")
                    )
                # posts = posts.exclude(type_data__object__has_key="relatedWith")
                # docs = index.posts_to_docs(posts)
                # c = len(docs)
                # index.insert_docs(docs)
                # self.stdout.write(self.style.SUCCESS(f"indexed {c} posts."))

            case "search":
                q = JournalQueryParser("" if query == "-" else query, page_size=100)
                q.facet_by = ["item_class", "piece_class"]
                if owners:
                    q.filter("owner_id", owners)
                if item_class:
                    q.filter("item_class", item_class)
                if piece_class:
                    q.filter("piece_class", piece_class)
                r = index.search(q)
                self.stdout.write(self.style.SUCCESS(str(r)))
                self.stdout.write(f"{r.facet_by_item_class}")
                self.stdout.write(f"{r.facet_by_piece_class}")
                self.stdout.write(self.style.SUCCESS("matched posts:"))
                for post in r:
                    self.stdout.write(str(post))
                self.stdout.write(self.style.SUCCESS("matched pieces:"))
                for pc in r.pieces:
                    self.stdout.write(str(pc))
                self.stdout.write(self.style.SUCCESS("matched items:"))
                for i in r.items:
                    self.stdout.write(str(i))

            case "idx-catchup":
                hour = kwargs.get("hour")
                self.idx_catchup(hour)

            case "idx-sync":
                self.idx_sync(index, owners)

            case _:
                self.stdout.write(self.style.ERROR("action not found."))
