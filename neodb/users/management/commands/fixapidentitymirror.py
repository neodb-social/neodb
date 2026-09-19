from django.db import IntegrityError, transaction
from django.db.models import Model
from django.utils import timezone
from tqdm import tqdm

from common.management.base import SiteCommand
from takahe.models import Identity
from takahe.utils import Takahe
from users.models.apidentity import APIdentity

# Why a mirror row no longer matches the identity it mirrors
ORPHAN = "orphan"
STALE = "stale"


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


def merge_apidentity(stale: APIdentity, target: APIdentity) -> dict[str, int]:
    """
    Moves everything owned by a mirror row onto the one for the identity that
    now holds its handle, then retires it.

    The row is kept and marked deleted rather than removed: journal data
    protects its owner from deletion, and a kept row can still be read while
    checking what a repair did.
    """
    if stale.pk == target.pk:
        raise ValueError("Cannot merge a mirror row into itself")
    if stale.local or target.local:
        raise ValueError("Cannot merge local identities")
    if stale.user_id:
        raise ValueError(f"APIdentity {stale.pk} belongs to a local user")
    moved: dict[str, int] = {}
    with transaction.atomic():
        for rel in APIdentity._meta.related_objects:
            model, field_name = relation_target(rel)
            rows = model._base_manager.filter(**{field_name: stale})
            for pk in list(rows.values_list("pk", flat=True)):
                row = model._base_manager.filter(pk=pk)
                try:
                    with transaction.atomic():
                        row.update(**{field_name: target})
                except IntegrityError:
                    row.delete()
                    key = f"{model._meta.label} dropped"
                else:
                    key = f"{model._meta.label} moved"
                moved[key] = moved.get(key, 0) + 1
        stale.deleted = timezone.now()
        stale.save(update_fields=["deleted"])
    return moved


class Command(SiteCommand):
    help = (
        "Repairs APIdentity rows that no longer match the Takahe identity they mirror"
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
            help="Do not ask before merging mirror rows",
        )

    def handle(self, fix: bool, yes: bool, *args, **options):
        findings = []
        identities = APIdentity.objects.filter(
            local=False, deleted__isnull=True
        ).order_by("pk")
        for apidentity in tqdm(identities):
            finding = self.classify(apidentity)
            if finding:
                findings.append((apidentity, *finding))

        for apidentity, kind, holder in findings:
            line = f"{kind:7} {apidentity.pk} {apidentity.username}@{apidentity.domain_name}"
            if holder:
                line += f" -> identity {holder.pk} {holder.actor_uri}"
            else:
                line += " (no identity holds that handle, left alone)"
            references = apidentity_references(apidentity)
            if references:
                line += f" owns {references}"
            self.stdout.write(line)

        repairable = [f for f in findings if f[2]]
        self.stdout.write(f"\n{len(findings)} mismatched, {len(repairable)} repairable")
        if not fix or not repairable:
            if not fix and repairable:
                self.stdout.write("Re-run with --fix to repair them.")
            return
        if not yes:
            self.stdout.write(
                f"About to move what {len(repairable)} mirror rows own onto the "
                "identity that holds their handle, and mark them deleted."
            )
            if not input("Are you sure? [Y/N] ").upper().startswith("Y"):
                self.stdout.write("Nothing was changed.")
                return
        for apidentity, kind, holder in repairable:
            if holder is None:
                continue
            try:
                # Only now, because a scan must not write: the identity that
                # holds the handle may have no mirror row yet
                target = Takahe.get_or_create_remote_apidentity(holder)
                moved = merge_apidentity(apidentity, target)
            except ValueError as error:
                self.stdout.write(f"skipped {apidentity.pk}: {error}")
                continue
            self.stdout.write(f"merged {apidentity.pk} -> {target.pk} {moved or ''}")

    def classify(self, apidentity: APIdentity) -> tuple[str, Identity | None] | None:
        """
        Reports a mirror row that has drifted from the identity behind it, and
        the identity that should own what it holds.
        """
        identity = Identity.objects.filter(pk=apidentity.pk).first()
        if identity is None:
            kind = ORPHAN
        elif (identity.username, identity.domain_id) != (
            apidentity.username,
            apidentity.domain_name,
        ):
            kind = STALE
        else:
            return None
        if not apidentity.username or not apidentity.domain_name:
            return kind, None
        holder = (
            Identity.objects.filter(
                username=apidentity.username, domain_id=apidentity.domain_name
            )
            .exclude(pk=apidentity.pk)
            .first()
        )
        return kind, holder
