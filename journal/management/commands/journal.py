from argparse import RawTextHelpFormatter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from tqdm import tqdm

from catalog.models import Item
from journal.exporters.ndjson import NdjsonExporter
from journal.models import (
    Collection,
    Content,
    JournalIndex,
    Piece,
    Review,
    ShelfMember,
    update_journal_for_merged_item,
)
from journal.models.index import JournalQueryParser
from journal.models.itemlist import ListMember
from takahe.models import Post
from users.models import APIdentity, User

_CONFIRM = "confirm deleting collection? [Y/N] "

_HELP_TEXT = """
intergrity:     check and fix remaining journal for merged and deleted items
purge:          delete invalid data (visibility=99)
export:         run export task
idx-info:       show index information
idx-init:       check and create index if not exists
idx-destroy:    delete index
idx-alt:        update index schema
idx-delete:     delete docs in index
idx-reindex:    reindex docs
idx-search:     search docs in index
"""


class Command(BaseCommand):
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
                "idx-search",
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
        *args,
        **kwargs,
    ):
        self.verbose = verbose
        self.fix = fix
        self.batch_size = batch_size
        index = JournalIndex.instance()

        if owner:
            owners = list(
                APIdentity.objects.filter(username__in=owner, local=True).values_list(
                    "id", flat=True
                )
            )
        else:
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
                # index all posts first
                posts = Post.objects.filter(local=True).exclude(
                    state__in=["deleted", "deleted_fanned_out"]
                )
                if owners:
                    self.stdout.write(
                        self.style.SUCCESS(f"indexing for {len(owners)} users.")
                    )
                    posts = posts.filter(author_id__in=owners)
                c = 0
                pg = Paginator(posts.order_by("id"), self.batch_size)
                for p in tqdm(pg.page_range):
                    docs = index.posts_to_docs(pg.get_page(p).object_list)
                    c += len(docs)
                    index.replace_docs(docs)
                self.stdout.write(self.style.SUCCESS(f"indexed {c} docs."))
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
                    pieces = cls.objects.filter(local=True)
                    if owners:
                        pieces = pieces.filter(owner_id__in=owners)
                    c = 0
                    pg = Paginator(pieces.order_by("id"), self.batch_size)
                    for p in tqdm(pg.page_range):
                        pieces = [
                            p
                            for p in pg.get_page(p).object_list
                            if p.latest_post is None
                        ]
                        docs = index.pieces_to_docs(pieces)
                        c += len(docs)
                        index.replace_docs(docs)
                self.stdout.write(self.style.SUCCESS(f"indexed {c} docs."))
                # posts = posts.exclude(type_data__object__has_key="relatedWith")
                # docs = index.posts_to_docs(posts)
                # c = len(docs)
                # index.insert_docs(docs)
                # self.stdout.write(self.style.SUCCESS(f"indexed {c} posts."))

            case "idx-search":
                q = JournalQueryParser("" if query == "-" else query, page_size=100)
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

            case _:
                self.stdout.write(self.style.ERROR("action not found."))
