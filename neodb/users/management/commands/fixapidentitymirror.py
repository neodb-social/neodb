"""
Repairs APIdentity rows that no longer match the Takahe identity they mirror.

Run the Takahe side first, then this side, each as a scan and then a repair:

    takahe-manage fixidentityhandles
    takahe-manage fixidentityhandles --fix
    neodb-manage fixapidentitymirror
    neodb-manage fixapidentitymirror --fix

fixidentityhandles finds remote identities that cannot hold their handle,
merges each alias into the identity its actor document names, and leaves the
alias row emptied with Identity.canonical pointing at that identity. This
command then brings the mirror in line: an alias mirror's journal data moves
onto the canonical identity's mirror and the alias mirror is retired, a mirror
whose identity renamed is resynced, and one whose identity is gone is retired.
"""

from django.db import IntegrityError, transaction
from django.db.models import Model
from django.utils import timezone
from tqdm import tqdm

from common.management.base import SiteCommand
from journal.models import Shelf, Tag
from journal.models.itemlist import List
from takahe.models import Identity
from users.models.apidentity import APIdentity

# Why a mirror row no longer matches the identity it mirrors
ORPHAN = "orphan"
STALE = "stale"
# Its identity turned out to be a second URI for another one, which now holds
# everything the identity had on the Takahe side
ALIAS = "alias"
# An orphan that still owns something: retiring it would hide that data, and a
# handle is not good enough evidence to hand the data to somebody else
OWNED = "orphan-owns-data"

# Rows an identity holds one of per key. When both mirrors hold one, the
# alias's members move into the canonical's rather than the row itself.
CONTAINERS: dict[type[List], str] = {Shelf: "shelf_type", Tag: "title"}


def relation_target(rel) -> tuple[type[Model], str]:
    """The model referencing APIdentity and the field that does it."""
    if rel.many_to_many:
        for field in rel.through._meta.get_fields():
            if (
                getattr(field, "many_to_one", False)
                and field.related_model is APIdentity
            ):
                return rel.through, field.name
        raise ValueError(f"No APIdentity foreign key on {rel.through._meta.label}")
    return rel.related_model, rel.field.name


def apidentity_references(apidentity: APIdentity) -> dict[str, int]:
    """Counts every row pointing at this mirror row, by model."""
    counts: dict[str, int] = {}
    for rel in APIdentity._meta.related_objects:
        model, field_name = relation_target(rel)
        count = model._base_manager.filter(**{field_name: apidentity}).count()
        if count:
            counts[model._meta.label] = counts.get(model._meta.label, 0) + count
    return counts


def merge_apidentity(alias: APIdentity, canonical: APIdentity) -> dict[str, int]:
    """
    Moves every row alias owns onto canonical and retires alias.

    The Takahe side already did the same for posts and follows. A row only one
    of which can exist per owner, such as a shelf or a mark of one item, may
    clash with one canonical already holds. Nothing here can choose between
    them, so the whole merge rolls back for a person to look at.
    """
    if alias.pk == canonical.pk:
        raise ValueError("Cannot merge a mirror into itself")
    if alias.local or canonical.local or alias.user_id or canonical.user_id:
        raise ValueError("Cannot merge a local user's mirror")
    moved: dict[str, int] = {}
    with transaction.atomic():
        for model, key in CONTAINERS.items():
            for container in model.objects.filter(owner=alias):
                target = model.objects.filter(
                    owner=canonical, **{key: getattr(container, key)}
                ).first()
                if target is None:
                    continue
                try:
                    with transaction.atomic():
                        container.members.update(parent=target)
                except IntegrityError as error:
                    raise ValueError(
                        f"{model._meta.label} {container.pk} has a member "
                        f"{canonical.pk}'s already holds: {error}"
                    ) from error
                model._base_manager.filter(pk=container.pk).delete()
        for rel in APIdentity._meta.related_objects:
            model, field_name = relation_target(rel)
            rows = model._base_manager.filter(**{field_name: alias})
            for pk in list(rows.values_list("pk", flat=True)):
                try:
                    with transaction.atomic():
                        model._base_manager.filter(pk=pk).update(
                            **{field_name: canonical}
                        )
                except IntegrityError as error:
                    raise ValueError(
                        f"{model._meta.label} {pk} clashes with a row "
                        f"{canonical.pk} already has: {error}"
                    ) from error
                key = f"{model._meta.label} moved"
                moved[key] = moved.get(key, 0) + 1
        left = apidentity_references(alias)
        if left:
            raise ValueError(f"Mirror {alias.pk} still referenced by {left}")
        alias.deleted = timezone.now()
        alias.save(update_fields=["deleted"])
    return moved


class Command(SiteCommand):
    help = (
        "Repairs APIdentity rows that no longer match the Takahe identity they "
        "mirror. Run takahe-manage fixidentityhandles --fix first."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Perform the repairs. Without it nothing is written.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Do not ask before retiring mirror rows",
        )

    def handle(self, fix: bool, yes: bool, *args, **options):
        findings = []
        identities = APIdentity.objects.filter(
            local=False, deleted__isnull=True
        ).order_by("pk")
        for apidentity in tqdm(identities):
            kind = self.classify(apidentity)
            if kind:
                findings.append((apidentity, kind))

        for apidentity, kind in findings:
            line = (
                f"{kind:16} {apidentity.pk} "
                f"{apidentity.username}@{apidentity.domain_name}"
            )
            if kind in {OWNED, ALIAS}:
                references = apidentity_references(apidentity)
                if references:
                    line += f" owns {references}"
            self.stdout.write(line)

        repairable = [f for f in findings if f[1] != OWNED]
        owned = len(findings) - len(repairable)
        self.stdout.write(
            f"\n{len(findings)} mismatched, {len(repairable)} repairable"
            + (f", {owned} own data and need a person" if owned else "")
        )
        if not fix:
            if repairable:
                self.stdout.write("Re-run with --fix to repair them.")
            return
        if not repairable:
            return
        retiring = len([f for f in repairable if f[1] == ORPHAN])
        merging = len([f for f in repairable if f[1] == ALIAS])
        if (retiring or merging) and not yes:
            if retiring:
                self.stdout.write(
                    f"About to mark {retiring} mirror rows deleted, whose Takahe "
                    "identity is gone and which own nothing."
                )
            if merging:
                self.stdout.write(
                    f"About to retire {merging} alias mirror rows, moving what "
                    "they own onto the identity they alias."
                )
            if not input("Are you sure? [Y/N] ").upper().startswith("Y"):
                self.stdout.write("Nothing was changed.")
                return
        for apidentity, kind in repairable:
            if kind == ORPHAN:
                apidentity.deleted = timezone.now()
                apidentity.save(update_fields=["deleted"])
                self.stdout.write(f"retired {apidentity.pk}")
            elif kind == ALIAS:
                identity = Identity.objects.get(pk=apidentity.pk)
                canonical = APIdentity.from_takahe(identity.canonical)
                try:
                    moved = merge_apidentity(apidentity, canonical)
                except ValueError as error:
                    self.stdout.write(f"skipped {apidentity.pk}: {error}")
                    continue
                self.stdout.write(
                    f"merged {apidentity.pk} into {canonical.pk}: "
                    f"{moved or 'nothing to move'}"
                )
            elif kind == STALE:
                identity = Identity.objects.get(pk=apidentity.pk)
                apidentity.username = identity.username
                apidentity.domain_name = identity.domain_id
                apidentity.save(update_fields=["username", "domain_name"])
                self.stdout.write(
                    f"resynced {apidentity.pk} to "
                    f"{identity.username}@{identity.domain_id}"
                )

    def classify(self, apidentity: APIdentity) -> str | None:
        """
        Reports a mirror row that has drifted from the identity behind it.

        A handle says nothing about who should own the row's data, because an
        identity can rename and another can take the name it left. So a row
        whose identity still exists is resynced from that identity by primary
        key, and a row whose identity is gone is only retired, never handed on.
        """
        identity = Identity.objects.filter(pk=apidentity.pk).first()
        if identity is None:
            return OWNED if apidentity_references(apidentity) else ORPHAN
        if identity.canonical_id:
            return ALIAS
        if (identity.username, identity.domain_id) == (
            apidentity.username,
            apidentity.domain_name,
        ):
            return None
        if not identity.username and apidentity_references(apidentity):
            # An identity fixidentityhandles emptied. Copying its nulls over
            # would leave this row's data reachable only through a handle that
            # reads None@None, so it needs a person either way.
            return OWNED
        return STALE
