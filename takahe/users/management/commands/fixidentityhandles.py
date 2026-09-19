import ssl
from typing import Any
from urllib.parse import urlparse

import httpx
from core.files import SSRFAttemptError
from activities.models import Conversation
from core.json import json_from_response
from core.ld import canonicalise
from core.models import Config
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.db.models import Model
from pyld.jsonld import JsonLdError
from stator.exceptions import TryAgainLater
from users.models import Identity
from users.models.system_actor import SystemActor
from users.models.identity import IdentityStates, _remote_text

# What a handle-less identity turned out to be
UNREACHABLE = "unreachable"
ALIAS = "alias"
FREE = "free"
RELEASABLE = "releasable"
UNFIXABLE = "unfixable"
FOREIGN = "foreign"

REPAIRABLE = {ALIAS, FREE, RELEASABLE}


def same_origin(one: str, other: str) -> bool:
    """
    Whether two actor URIs come from the same host.

    Only the server an actor lives on may say that actor is an alias of
    another. Believing a server that names a different host's actor would let
    it move that actor's posts and followers onto whatever it points at, so a
    cross-host claim is reported instead, however genuine it may be: a server
    that really did move can say so in alsoKnownAs, and none of the ones seen
    doing this do.
    """
    return urlparse(one).hostname == urlparse(other).hostname


def relation_target(rel) -> tuple[type[Model], str]:
    """
    The model holding a reference to Identity and the field that holds it.

    For a many-to-many that is the through model, because the rows to move are
    its, not either end's.
    """
    if rel.many_to_many:
        for field in rel.through._meta.get_fields():
            if getattr(field, "many_to_one", False) and field.related_model is Identity:
                return rel.through, field.name
        raise ValueError(f"No Identity foreign key on {rel.through._meta.label}")
    return rel.related_model, rel.field.name


def identity_references(identity: Identity) -> dict[str, int]:
    """Counts every row pointing at this identity, by model."""
    counts: dict[str, int] = {}
    for rel in Identity._meta.related_objects:
        model, field_name = relation_target(rel)
        count = model._base_manager.filter(**{field_name: identity}).count()
        if count:
            label = model._meta.label
            counts[label] = counts.get(label, 0) + count
    return counts


def merge_identity(alias: Identity, canonical: Identity) -> dict[str, int]:
    """
    Moves every row referencing alias onto canonical and retires alias.

    The row is kept, not deleted. NeoDB mirrors identities by primary key and
    lives in another database this process cannot reach, so deleting one here
    can leave a review or a collection owned by an identity that no longer
    exists. An emptied row with no handle costs nothing and pruneidentities
    can take it later.
    """
    if alias.pk == canonical.pk:
        raise ValueError("Cannot merge an identity into itself")
    if alias.local or canonical.local:
        raise ValueError("Cannot merge local identities")
    if alias.users.exists():
        raise ValueError(f"Identity {alias.pk} belongs to a local user")
    if canonical.canonical_id:
        raise ValueError(
            f"Identity {canonical.pk} is itself an alias of "
            f"{canonical.canonical_id}, merge into that instead"
        )
    if alias.restriction > canonical.restriction:
        # Moving the posts of a limited or blocked identity onto an
        # unrestricted one would quietly undo a moderator's decision
        raise ValueError(
            f"Identity {alias.pk} is restricted ({alias.restriction}) and "
            f"{canonical.pk} is not"
        )
    moved: dict[str, int] = {}
    conversations = list(
        Conversation.objects.filter(participants=alias).values_list("pk", flat=True)
    )
    with transaction.atomic():
        for rel in Identity._meta.related_objects:
            model, field_name = relation_target(rel)
            rows = model._base_manager.filter(**{field_name: alias})
            for pk in list(rows.values_list("pk", flat=True)):
                row = model._base_manager.filter(pk=pk)
                try:
                    with transaction.atomic():
                        row.update(**{field_name: canonical})
                except IntegrityError as error:
                    # The canonical identity has its own row where only one
                    # can exist. They are not necessarily the same: two notes
                    # about one person hold different text, and two follows
                    # hold different states. Nothing here can choose between
                    # them, so the whole merge rolls back for a person to look
                    # at.
                    raise ValueError(
                        f"{model._meta.label} {pk} clashes with a row "
                        f"{canonical.pk} already has: {error}"
                    ) from error
                key = f"{model._meta.label} moved"
                moved[key] = moved.get(key, 0) + 1
        for conversation in Conversation.objects.filter(pk__in=conversations):
            participants = set(conversation.participants.values_list("pk", flat=True))
            new_hash = Conversation.compute_participant_hash(participants)
            if new_hash == conversation.participant_hash:
                continue
            if (
                Conversation.objects.filter(participant_hash=new_hash)
                .exclude(pk=conversation.pk)
                .exists()
            ):
                # Both identities already talk to the same people, and merging
                # the two threads is not this command's call to make
                raise ValueError(
                    f"Identity {alias.pk} shares conversation {conversation.pk} "
                    "with the identity it merges into"
                )
            Conversation.objects.filter(pk=conversation.pk).update(
                participant_hash=new_hash
            )
        left = identity_references(alias)
        if left:
            raise ValueError(f"Identity {alias.pk} still referenced by {left}")
        # Give up the handle, which is the point of the whole exercise, and
        # record where the row's actor really lives so that peers still
        # addressing it by this URI resolve to that identity. The guards in
        # fetch_actor stop the emptied row taking the handle again.
        Identity.objects.filter(pk=alias.pk).update(
            username=None, domain=None, canonical=canonical
        )
        # Anything that pointed at the alias now points here instead
        Identity.objects.filter(canonical=alias).update(canonical=canonical)
    return moved


class Command(BaseCommand):
    help = "Finds identities that cannot hold their handle, and merges the aliases holding it"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Perform the repairs. Without it nothing is written.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Do not ask before merging rows",
        )
        parser.add_argument(
            "--number",
            "-n",
            type=int,
            default=200,
            help="The maximum number of identities to examine",
        )
        parser.add_argument(
            "--actor",
            help="Examine only this actor URI, whatever state it is in",
        )

    def handle(
        self,
        fix: bool,
        yes: bool,
        number: int,
        actor: str | None,
        *args,
        **options,
    ):
        # Only middleware and the stator runner load this, and a probe needs
        # the system actor's key to sign with
        Config.system = Config.load_system()
        if actor:
            identities = Identity.objects.filter(actor_uri=actor)
        else:
            identities = Identity.objects.filter(
                local=False, username__isnull=True
            ).order_by("pk")
        identities = list(identities[:number])
        self.stdout.write(f"Examining {len(identities)} identities...")

        findings = []
        for identity in identities:
            finding = self.classify(identity)
            findings.append((identity, *finding))
            self.report(identity, *finding)

        repairable = [f for f in findings if f[1] in REPAIRABLE]
        self.stdout.write("")
        for kind in [FREE, RELEASABLE, ALIAS, FOREIGN, UNFIXABLE, UNREACHABLE]:
            count = len([f for f in findings if f[1] == kind])
            if count:
                self.stdout.write(f"{kind}: {count}")
        if not fix:
            self.stdout.write(
                f"\n{len(repairable)} repairable. Re-run with --fix to repair them."
            )
            return
        if not repairable:
            return
        merges = len([f for f in repairable if f[1] in {ALIAS, RELEASABLE}])
        if merges and not yes:
            self.stdout.write(
                f"\nAbout to empty {merges} identity rows. Their posts and "
                "follows move to the identity they alias, which then takes "
                "back the handle."
            )
            if not input("Are you sure? [Y/N] ").upper().startswith("Y"):
                self.stdout.write("Nothing was changed.")
                return
        for identity, kind, other, target_uri in repairable:
            self.repair(identity, kind, other, target_uri)

    def classify(self, identity: Identity) -> tuple[str, Identity | None, str | None]:
        """
        Decides what stands between this identity and its handle.

        Reads the remote actor rather than the row, because a row with no
        handle stores nothing that says which one it wants.
        """
        document = self.probe(identity.actor_uri)
        if document is None:
            return UNREACHABLE, None, None
        document_id = document.get("id")
        if isinstance(document_id, str) and document_id != identity.actor_uri:
            # This row is the alias; the actor it names is the real one
            if not same_origin(identity.actor_uri, document_id):
                return (
                    FOREIGN,
                    Identity.objects.filter(actor_uri=document_id).first(),
                    document_id,
                )
            canonical = Identity.objects.filter(actor_uri=document_id).first()
            return ALIAS, canonical, document_id
        handle = self.wanted_handle(identity.actor_uri, document)
        if handle is None:
            return UNFIXABLE, None, None
        username, domain = handle
        holder = (
            Identity.objects.filter(username=username, domain_id=domain)
            .exclude(pk=identity.pk)
            .first()
        )
        if holder is None:
            return FREE, None, None
        holder_document = self.probe(holder.actor_uri)
        if holder_document is None:
            return UNREACHABLE, holder, None
        if holder_document.get("id") == identity.actor_uri:
            # The holder is an alias of this identity, so it can give the
            # handle back
            if not same_origin(identity.actor_uri, holder.actor_uri):
                return FOREIGN, holder, None
            return RELEASABLE, holder, None
        # Two actors that really are distinct, such as a Lemmy user and a
        # community of the same name. The schema cannot hold both.
        return UNFIXABLE, holder, None

    def probe(self, actor_uri: str) -> dict[str, Any] | None:
        """Reads a remote actor without touching any row."""
        if (actor_uri or "").lower().split(":")[0] not in ["http", "https"]:
            return None
        try:
            response = SystemActor().signed_request(method="get", uri=actor_uri)
        except (
            httpx.RequestError,
            ssl.SSLCertVerificationError,
            SSRFAttemptError,
        ):
            return None
        if response.status_code >= 400:
            return None
        content_type = response.headers.get("content-type")
        if content_type and "html" in content_type:
            return None
        try:
            return canonicalise(json_from_response(response), include_security=True)
        except ValueError, JsonLdError:
            return None

    def wanted_handle(
        self, actor_uri: str, document: dict[str, Any]
    ) -> tuple[str, str] | None:
        """
        The handle this actor would claim, by the same rules as fetch_actor.
        """
        username = _remote_text(document.get("preferredUsername"))
        hostname = urlparse(actor_uri).hostname
        if not username or not hostname:
            return None
        try:
            webfinger_actor, webfinger_handle = Identity.fetch_webfinger(
                f"{username}@{hostname}"
            )
        except TryAgainLater, ValueError:
            webfinger_actor = webfinger_handle = None
        if webfinger_handle and webfinger_actor == actor_uri:
            webfinger_username, webfinger_domain = webfinger_handle.split("@")
            return webfinger_username, webfinger_domain.lower()
        return username, hostname.lower()

    def report(
        self, identity: Identity, kind: str, other: Identity | None, target_uri=None
    ):
        line = f"{kind:12} {identity.actor_uri}"
        if other:
            line += f"\n{'':12} handle held by {other.actor_uri}"
            references = identity_references(other)
            if references:
                line += f"\n{'':12} which owns {references}"
        self.stdout.write(line)

    def repair(
        self,
        identity: Identity,
        kind: str,
        other: Identity | None,
        target_uri: str | None,
    ):
        if kind == FREE:
            self.stdout.write(f"refetching {identity.actor_uri}")
            identity.transition_perform(IdentityStates.outdated)
            return
        if kind == RELEASABLE and other:
            alias, canonical = other, identity
        elif kind == ALIAS and (other or target_uri):
            alias = identity
            # The actor this row aliases may not be stored yet; it has to be,
            # to hold what moves off the alias
            canonical = other or Identity.by_actor_uri(target_uri, create=True)
        else:
            return
        self.stdout.write(f"merging {alias.actor_uri} into {canonical.actor_uri}")
        try:
            moved = merge_identity(alias, canonical)
        except ValueError as error:
            self.stdout.write(f"  skipped: {error}")
            return
        self.stdout.write(f"  {moved or 'nothing to move'}")
        canonical.transition_perform(IdentityStates.outdated)
