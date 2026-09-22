from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from catalog.models import Edition
from journal.models import Comment, Shelf, ShelfMember, ShelfType, Tag, TagMember
from takahe.models import Domain, Identity
from users.management.commands.fixapidentitymirror import (
    Command,
    apidentity_references,
)
from users.models.apidentity import APIdentity


def run(**kwargs) -> str:
    out = StringIO()
    call_command("fixapidentitymirror", stdout=out, **kwargs)
    return out.getvalue()


def make_identity(pk: int, actor_uri: str, username=None, domain=None) -> Identity:
    return Identity.objects.create(
        pk=pk,
        actor_uri=actor_uri,
        username=username,
        domain=domain,
        local=False,
        private_key="",
        public_key="",
    )


def make_mirror(pk: int, username: str, domain_name: str) -> APIdentity:
    return APIdentity.objects.create(
        pk=pk,
        user=None,
        local=False,
        username=username,
        domain_name=domain_name,
        anonymous_viewable=False,
    )


def make_alias(
    alias_pk: int, canonical_pk: int, username: str = "ruben"
) -> tuple[Identity, Identity]:
    """A merged alias and the identity it now resolves to, as Takahe leaves them."""
    domain = Domain.get_remote_domain("example.com")
    canonical = make_identity(
        canonical_pk, f"https://example.com/u{canonical_pk}", username, domain
    )
    alias = make_identity(alias_pk, f"https://example.com/users/u{alias_pk}")
    Identity.objects.filter(pk=alias.pk).update(canonical=canonical)
    return alias, canonical


def shelve(owner: APIdentity, item: Edition, shelf_type: ShelfType) -> ShelfMember:
    shelf = Shelf.objects.get_or_create(owner=owner, shelf_type=shelf_type)[0]
    return ShelfMember.objects.create(
        owner=owner, parent=shelf, item=item, position=0, local=False
    )


@pytest.mark.django_db(databases="__all__")
class TestFixAPIdentityMirror:
    def test_orphan_owning_nothing_is_retired(self):
        """
        Merging an alias away on the Takahe side leaves its mirror row behind,
        still claiming a handle another identity now holds. get_remote() takes
        the first match, so the stale row can answer for the wrong identity.
        """
        domain = Domain.get_remote_domain("example.com")
        make_identity(101, "https://example.com/ruben", username="ruben", domain=domain)
        orphan = make_mirror(102, "ruben", "example.com")

        output = run(fix=True, yes=True)

        assert "orphan" in output
        orphan.refresh_from_db()
        assert orphan.deleted is not None

    def test_stale_mirror_is_resynced_from_its_own_identity(self):
        """
        A handle is no evidence of who should own the data: the identity is
        the one behind this very row, found by primary key, never whoever
        happens to hold the handle now.
        """
        domain = Domain.get_remote_domain("example.com")
        make_identity(
            103, "https://example.com/renamed", username="renamed", domain=domain
        )
        # Somebody else took the name this mirror still claims
        make_identity(
            104, "https://example.com/oldname", username="oldname", domain=domain
        )
        make_mirror(104, "oldname", "example.com")
        mirror = make_mirror(103, "oldname", "example.com")

        run(fix=True, yes=True)

        mirror.refresh_from_db()
        assert mirror.username == "renamed"
        assert mirror.deleted is None

    def test_stale_mirror_is_resynced_when_nobody_took_the_old_handle(self):
        """
        Nothing needs to hold the old handle for the row to be wrong: the
        identity behind it renamed, and the mirror still says the old name.
        Raised on PR 1917 by Sentry's reviewer.
        """
        domain = Domain.get_remote_domain("example.com")
        make_identity(
            109, "https://example.com/renamed", username="renamed", domain=domain
        )
        mirror = make_mirror(109, "oldname", "example.com")

        output = run(fix=True, yes=True)

        assert "stale" in output
        mirror.refresh_from_db()
        assert mirror.username == "renamed"
        assert mirror.domain_name == "example.com"
        assert mirror.deleted is None

    def test_orphan_that_owns_data_is_only_reported(self):
        """
        Retiring a row that owns journal data would hide it, and no handle is
        good enough reason to give that data to another identity.
        """
        orphan = make_mirror(105, "someone", "example.com")
        tag = Tag.objects.create(owner=orphan, title="kept")

        output = run(fix=True, yes=True)

        assert "orphan-owns-data" in output
        orphan.refresh_from_db()
        assert orphan.deleted is None
        tag.refresh_from_db()
        assert tag.owner_id == orphan.pk

    def test_emptied_identity_owning_data_is_not_resynced_to_nulls(self):
        """
        fixidentityhandles leaves a merged alias with no handle. Copying that
        over a mirror that owns journal data would leave the data reachable
        only through a handle reading None@None.
        """
        make_identity(110, "https://example.com/users/ruben")
        mirror = make_mirror(110, "ruben", "example.com")
        Tag.objects.create(owner=mirror, title="kept")

        output = run(fix=True, yes=True)

        assert "orphan-owns-data" in output
        mirror.refresh_from_db()
        assert mirror.username == "ruben"
        assert mirror.deleted is None

    def test_alias_mirror_data_moves_onto_the_canonical_mirror(self):
        """
        Takahe already moved the posts and follows. What NeoDB holds against
        the alias id, marks, tags and comments, belongs to the same person and
        follows Identity.canonical to the mirror of the identity that now
        holds everything, so it stays reachable under a handle that exists.
        """
        make_alias(120, 121)
        alias_mirror = make_mirror(120, "ruben", "example.com")
        canonical_mirror = make_mirror(121, "ruben", "example.com")
        book = Edition.objects.create(title="A Book")
        other = Edition.objects.create(title="Another Book")
        # Both mirrors hold the same shelf, which the schema allows only once
        # per owner, so the mark moves into the shelf canonical already has
        mark = shelve(alias_mirror, book, ShelfType.COMPLETE)
        kept = shelve(canonical_mirror, other, ShelfType.COMPLETE)
        # Same for a tag of the same title; a tag only the alias has moves whole
        shared = Tag.objects.create(owner=alias_mirror, title="shared", local=False)
        TagMember.objects.create(
            owner=alias_mirror, parent=shared, item=book, position=0, local=False
        )
        theirs = Tag.objects.create(owner=canonical_mirror, title="shared", local=False)
        only = Tag.objects.create(owner=alias_mirror, title="only", local=False)
        comment = Comment.objects.create(
            owner=alias_mirror, item=book, text="hi", local=False
        )

        output = run(fix=True, yes=True)

        assert "alias" in output
        assert "merged 120 into 121" in output
        alias_mirror.refresh_from_db()
        assert alias_mirror.deleted is not None
        assert apidentity_references(alias_mirror) == {}
        mark.refresh_from_db()
        assert mark.owner_id == canonical_mirror.pk
        assert mark.parent == kept.parent
        assert Shelf.objects.filter(owner=canonical_mirror).count() == 1
        assert list(
            TagMember.objects.filter(parent=theirs).values_list("item_id", flat=True)
        ) == [book.pk]
        assert not Tag.objects.filter(pk=shared.pk).exists()
        only.refresh_from_db()
        assert only.owner_id == canonical_mirror.pk
        comment.refresh_from_db()
        assert comment.owner_id == canonical_mirror.pk

    def test_alias_merge_rewrites_the_search_index(
        self, django_capture_on_commit_callbacks
    ):
        """
        The rows move by queryset update, which Piece.save() never sees, so
        every indexed document would go on naming the retired alias as owner
        and the canonical identity's data would stay invisible in search. The
        rewrite goes through the retrying worker once the merge has committed,
        so a Typesense failure does not leave the index half done.
        """
        make_alias(126, 127)
        alias_mirror = make_mirror(126, "ruben", "example.com")
        canonical_mirror = make_mirror(127, "ruben", "example.com")
        book = Edition.objects.create(title="A Book")
        mark = shelve(alias_mirror, book, ShelfType.COMPLETE)
        shelve(canonical_mirror, Edition.objects.create(title="B"), ShelfType.COMPLETE)
        comment = Comment.objects.create(
            owner=alias_mirror, item=book, text="hi", local=False
        )
        alias_shelf_pk = Shelf.objects.get(owner=alias_mirror).pk

        with (
            patch(
                "users.management.commands.fixapidentitymirror."
                "JournalIndex.enqueue_replace_pieces"
            ) as enqueue,
            django_capture_on_commit_callbacks(execute=True),
        ):
            run(fix=True, yes=True)

        enqueue.assert_called_once()
        (replaced,), _ = enqueue.call_args
        assert {mark.pk, comment.pk, alias_shelf_pk} <= set(replaced)

    def test_merged_mark_keeps_its_own_visibility(self):
        """
        Shelves are public by default while a mark carries the visibility of
        the post it came from. Taking the shelf's visibility on the way in
        would publish a followers-only mark.
        """
        make_alias(130, 131)
        alias_mirror = make_mirror(130, "ruben", "example.com")
        canonical_mirror = make_mirror(131, "ruben", "example.com")
        book = Edition.objects.create(title="A Book")
        mark = shelve(alias_mirror, book, ShelfType.COMPLETE)
        ShelfMember.objects.filter(pk=mark.pk).update(visibility=1)
        shelve(canonical_mirror, Edition.objects.create(title="B"), ShelfType.COMPLETE)

        run(fix=True, yes=True)

        mark.refresh_from_db()
        assert mark.owner_id == canonical_mirror.pk
        assert mark.visibility == 1

    def test_alias_mirror_is_retired_and_the_canonical_mirror_created(self):
        """
        The canonical identity may never have reached NeoDB. An alias that
        owns nothing is retired rather than resynced to a handle reading
        None@None, which the scan would otherwise report on every run.
        """
        make_alias(122, 123)
        alias_mirror = make_mirror(122, "ruben", "example.com")

        output = run(fix=True, yes=True)

        assert "nothing to move" in output
        alias_mirror.refresh_from_db()
        assert alias_mirror.deleted is not None
        created = APIdentity.objects.get(pk=123)
        assert (created.username, created.domain_name) == ("ruben", "example.com")
        assert "0 mismatched" in run()

    def test_alias_merge_rolls_back_on_a_clash(self):
        """
        Two marks of one item are not necessarily the same mark, and nothing
        here can choose between them, so the whole merge is left for a
        person, with the alias still owning everything it did.
        """
        make_alias(124, 125)
        alias_mirror = make_mirror(124, "ruben", "example.com")
        canonical_mirror = make_mirror(125, "ruben", "example.com")
        book = Edition.objects.create(title="A Book")
        mark = shelve(alias_mirror, book, ShelfType.WISHLIST)
        shelve(canonical_mirror, book, ShelfType.COMPLETE)
        comment = Comment.objects.create(
            owner=alias_mirror, item=book, text="hi", local=False
        )

        output = run(fix=True, yes=True)

        assert "skipped 124" in output
        alias_mirror.refresh_from_db()
        assert alias_mirror.deleted is None
        mark.refresh_from_db()
        assert mark.owner_id == alias_mirror.pk
        comment.refresh_from_db()
        assert comment.owner_id == alias_mirror.pk

    def test_alias_whose_canonical_went_away_is_skipped(self):
        """
        Every row is classified before any is repaired, and the canonical
        identity can be deleted in between, which nulls the alias's pointer.
        One such row must not abort the rest of the batch.
        """
        alias, canonical = make_alias(132, 133)
        make_mirror(132, "ruben", "example.com")
        make_alias(134, 135, "ruth")
        other = make_mirror(134, "ruth", "example.com")
        original = Command.classify

        def classify_then_delete(command, apidentity):
            kind = original(command, apidentity)
            if apidentity.pk == 132:
                Identity.objects.filter(pk=canonical.pk).delete()
            return kind

        with patch.object(Command, "classify", classify_then_delete):
            output = run(fix=True, yes=True)

        assert "skipped 132" in output
        assert "merged 134 into 135" in output
        other.refresh_from_db()
        assert other.deleted is not None

    def test_alias_is_not_merged_onto_a_retired_mirror(self):
        """
        from_takahe hands back whatever row holds the canonical pk, retired or
        not, and data moved onto a retired row is hidden as surely as data on
        an orphan.
        """
        make_alias(128, 129)
        alias_mirror = make_mirror(128, "ruben", "example.com")
        retired = make_mirror(129, "ruben", "example.com")
        retired.deleted = timezone.now()
        retired.save(update_fields=["deleted"])
        book = Edition.objects.create(title="A Book")
        comment = Comment.objects.create(
            owner=alias_mirror, item=book, text="hi", local=False
        )

        output = run(fix=True, yes=True)

        assert "skipped 128" in output
        comment.refresh_from_db()
        assert comment.owner_id == alias_mirror.pk

    def test_scan_only_by_default(self):
        domain = Domain.get_remote_domain("example.com")
        make_identity(106, "https://example.com/ruben", username="ruben", domain=domain)
        orphan = make_mirror(107, "ruben", "example.com")

        output = run()

        assert "1 repairable" in output
        orphan.refresh_from_db()
        assert orphan.deleted is None

    def test_consistent_mirror_is_not_reported(self):
        domain = Domain.get_remote_domain("example.com")
        make_identity(108, "https://example.com/ok", username="ok", domain=domain)
        make_mirror(108, "ok", "example.com")

        output = run()

        assert "0 mismatched" in output
