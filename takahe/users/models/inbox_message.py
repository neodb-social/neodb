import base64
import logging

from django.db import models
from pyld.jsonld import JsonLdError

from activities.models.hashtag import Hashtag
from core.exceptions import ActivityPubError
from core.ld import get_first_concrete_type
from core.signatures import (
    HttpSignature,
    LDSignature,
    VerificationError,
    VerificationFormatError,
)
from stator.models import State, StateField, StateGraph, StatorModel

logger = logging.getLogger(__name__)


class InboxMessageStates(StateGraph):
    received = State(try_interval=300, delete_after=86400 * 3)
    processed = State(externally_progressed=True, delete_after=86400)
    errored = State(externally_progressed=True, delete_after=86400)

    received.transitions_to(processed)
    received.transitions_to(errored)

    @classmethod
    def _ensure_key(cls, identity) -> bool:
        """
        Fetch the actor if its public key is not yet cached.
        Returns True if a key is available after the attempt.

        TODO: stale-key retry missing. This method only fetches when the key
        is absent. If the key IS present but stale (remote actor rotated keys
        after we cached them), deferred verification will fail permanently.

        """
        if identity.public_key:
            return True
        try:
            identity.fetch_actor()
        except Exception:
            pass
        return bool(identity.public_key)

    @classmethod
    def _verify_deferred(cls, instance: "InboxMessage") -> bool | None:
        """
        Verify signatures that were deferred because the actor's public
        key was not available at inbox time.

        Returns True if verified, False if verification failed (bad sig),
        or None if keys are still unavailable (should retry later).

        Sig types processed in order:
          relay_http_sig — relay HTTP sig; on success, remove and fall through to ld_sig
          http_sig       — direct-delivery HTTP sig; on success, return True
          ld_sig         — LD sig; on success, return True
        """
        from users.models import Identity  # avoid circular import

        deferred_sigs = instance.metadata
        if not deferred_sigs:
            return False

        for sig_type in ["relay_http_sig", "http_sig", "ld_sig"]:
            if sig_type not in deferred_sigs:
                continue

            sig_data = deferred_sigs[sig_type]
            actor_uri = (
                sig_data.get("relay_uri")
                or sig_data.get("actor_uri")
                or sig_data.get("creator_uri")
            )

            if sig_type == "ld_sig" and actor_uri != instance.message.get("actor"):
                logger.warning(
                    "Inbox: Deferred LD sig creator %s does not match actor %s",
                    actor_uri,
                    instance.message.get("actor"),
                )
                return False

            identity = Identity.by_actor_uri(actor_uri, create=True)
            if not cls._ensure_key(identity):
                continue  # key still unavailable; retry later

            try:
                if sig_type == "ld_sig":
                    # Prefer raw_document if stored; fall back to canonicalized
                    # instance.message for older deferred entries.
                    ld_doc = sig_data.get("raw_document") or instance.message
                    LDSignature.verify_signature(ld_doc, identity.public_key)
                else:
                    HttpSignature.verify_signature(
                        base64.b64decode(sig_data["signature"]),
                        sig_data["headers_string"],
                        identity.public_key,
                    )
                logger.debug(
                    "Inbox: Deferred %s verification succeeded for %s",
                    sig_type,
                    actor_uri,
                )
            except VerificationError, VerificationFormatError:
                logger.warning(
                    "Inbox: Deferred %s verification failed for %s",
                    sig_type,
                    actor_uri,
                )
                return False

            # relay_http_sig verified: remove it and fall through to ld_sig.
            # ld_sig must still pass before the message is accepted.
            if sig_type == "relay_http_sig":
                deferred_sigs = {
                    k: v for k, v in deferred_sigs.items() if k != "relay_http_sig"
                }
                InboxMessage.objects.filter(pk=instance.pk).update(
                    metadata=deferred_sigs or None
                )
                if not deferred_sigs:
                    return True  # LD sig was verified at inbox time
                continue

            return True

        # No signature could be verified yet (keys still unavailable)
        return None

    @classmethod
    def handle_received(cls, instance: "InboxMessage"):
        from activities.models import Post, PostInteraction, TimelineEvent
        from users.models import Block, Follow, Identity, Relay, Report
        from users.services import IdentityService

        # If this message has deferred verification data, verify
        # the signature before processing.
        if instance.metadata:
            result = cls._verify_deferred(instance)
            if result is None:
                # Keys still unavailable, retry later
                return None
            if result is False:
                return cls.errored
            # Verified -- clear deferred data and proceed
            InboxMessage.objects.filter(pk=instance.pk).update(metadata=None)

        try:
            match instance.message_type:
                case "follow":
                    Follow.handle_request_ap(instance.message)
                case "block":
                    Block.handle_ap(instance.message)
                case "announce":
                    # Ignore Lemmy-specific likes/dislikes and their undos
                    # for perf reasons (we don't tally group-relayed votes)
                    if instance.message_object_type in ["like", "dislike", "undo"]:
                        return cls.processed
                    if instance.message_object_type in ["create", "update", "delete"]:
                        # Group actors (e.g. Lemmy communities) relay their
                        # members' activities to followers inside Announce
                        Post.handle_announced_activity_ap(instance.message)
                    else:
                        PostInteraction.handle_ap(instance.message)
                case "like":
                    PostInteraction.handle_ap(instance.message)
                case "create":
                    match instance.message_object_type:
                        case "note":
                            if instance.message_object_has_content_or_attachment:
                                Post.handle_create_ap(instance.message)
                            else:
                                # Bare Notes (no content, no attachment) are
                                # Interaction candidates, e.g. poll votes.
                                PostInteraction.handle_ap(instance.message)
                        case "question":
                            Post.handle_create_ap(instance.message)
                        case unknown:
                            if unknown in Post.Types.names:
                                Post.handle_create_ap(instance.message)
                case "update":
                    match instance.message_object_type:
                        case "note":
                            Post.handle_update_ap(instance.message)
                        case "person":
                            Identity.handle_update_ap(instance.message)
                        case "service":
                            Identity.handle_update_ap(instance.message)
                        case "group":
                            Identity.handle_update_ap(instance.message)
                        case "organization":
                            Identity.handle_update_ap(instance.message)
                        case "application":
                            Identity.handle_update_ap(instance.message)
                        case "question":
                            Post.handle_update_ap(instance.message)
                        case unknown:
                            if unknown in Post.Types.names:
                                Post.handle_update_ap(instance.message)
                case "accept":
                    match instance.message_object_type:
                        case "follow":
                            if Relay.is_ap_message_for_relay(instance.message):
                                Relay.handle_accept_ap(instance.message)
                            else:
                                Follow.handle_accept_ap(instance.message)
                        case None:
                            # It's a string object, but these will only be for Follows
                            Follow.handle_accept_ap(instance.message)
                        case unknown:
                            return cls.errored
                case "reject":
                    match instance.message_object_type:
                        case "follow":
                            if Relay.is_ap_message_for_relay(instance.message):
                                Relay.handle_reject_ap(instance.message)
                            else:
                                Follow.handle_reject_ap(instance.message)
                        case None:
                            # It's a string object, but these will only be for Follows
                            Follow.handle_reject_ap(instance.message)
                        case unknown:
                            return cls.errored
                case "undo":
                    match instance.message_object_type:
                        case "follow":
                            Follow.handle_undo_ap(instance.message)
                        case "block":
                            Block.handle_undo_ap(instance.message)
                        case "like":
                            PostInteraction.handle_undo_ap(instance.message)
                        case "announce":
                            PostInteraction.handle_undo_ap(instance.message)
                        case "http://litepub.social/ns#emojireact":
                            # We're ignoring emoji reactions for now
                            pass
                        case unknown:
                            return cls.errored
                case "delete":
                    # Forward before deleting: targeting needs our copy of
                    # the post (no-op unless the inbox kept a raw document)
                    if instance.raw_document:
                        Post.forward_activity_ap(
                            instance.message, instance.raw_document
                        )
                    # If there is no object type, we need to see if it's a profile or a post
                    if not isinstance(instance.message["object"], dict):
                        if Identity.objects.filter(
                            actor_uri=instance.message["object"]
                        ).exists():
                            Identity.handle_delete_ap(instance.message)
                        elif Post.objects.filter(
                            object_uri=instance.message["object"]
                        ).exists():
                            Post.handle_delete_ap(instance.message)
                        else:
                            # It is presumably already deleted
                            pass
                    else:
                        match instance.message_object_type:
                            case "tombstone":
                                Post.handle_delete_ap(instance.message)
                            case "note":
                                Post.handle_delete_ap(instance.message)
                            case unknown:
                                return cls.errored
                case "add":
                    match instance.message_object_type:
                        case "hashtag":
                            Hashtag.handle_add_ap(instance.message)
                        case unknown:
                            PostInteraction.handle_add_ap(instance.message)
                case "remove":
                    match instance.message_object_type:
                        case "hashtag":
                            Hashtag.handle_remove_ap(instance.message)
                        case unknown:
                            PostInteraction.handle_remove_ap(instance.message)
                case "quoterequest" | "https://w3id.org/fep/044f#quoterequest":
                    Post.handle_quote_request_ap(instance.message)
                case "move":
                    # We're ignoring moves for now
                    pass
                case "http://litepub.social/ns#emojireact":
                    # We're ignoring emoji reactions for now
                    pass
                case "flag":
                    # Received reports
                    Report.handle_ap(instance.message)
                case "__internal__":
                    match instance.message_object_type:
                        case "deleteidentity":
                            try:
                                i = Identity.by_actor_uri(
                                    instance.message["object"]["actor"]
                                )
                                if not i.local:
                                    i.delete()
                                elif not i.deleted:
                                    i.mark_deleted()
                            except Exception as e:
                                print(e)
                        case "fetchidentity":
                            try:
                                Identity.by_handle(
                                    instance.message["object"]["handle"], fetch=True
                                )
                            except Exception as e:
                                print(e)
                        case "fetchpost":
                            Post.handle_fetch_internal(instance.message["object"])
                        case "fetchreplies":
                            Post.handle_fetch_replies(instance.message["object"])
                        case "searchurl":
                            from activities.services.search import SearchService

                            handle = instance.message["object"].get("handle")
                            identity = (
                                Identity.by_handle(handle, fetch=True)
                                if handle
                                else None
                            )
                            url = instance.message["object"]["url"]
                            ss = SearchService(url, identity)
                            ss.search_url()
                        case "cleartimeline":
                            TimelineEvent.handle_clear_timeline(
                                instance.message["object"]
                            )
                        case "addfollow":
                            IdentityService.handle_internal_add_follow(
                                instance.message["object"]
                            )
                        case "syncactor":
                            IdentityService.handle_internal_sync_actor(
                                instance.message["object"]
                            )
                        case unknown:
                            return cls.errored
                case unknown:
                    return cls.errored
            # Replies (and their edits) to local threads are forwarded to
            # the thread author's followers once ingested (no-op unless
            # the inbox kept a raw LD-signed document)
            if instance.raw_document and instance.message_type in [
                "create",
                "update",
            ]:
                Post.forward_activity_ap(instance.message, instance.raw_document)
            return cls.processed
        except ActivityPubError, JsonLdError:
            return cls.errored


class InboxMessage(StatorModel):
    """
    an incoming inbox message that needs processing.

    Yes, this is kind of its own message queue built on the state graph system.
    It's fine. It'll scale up to a decent point.
    """

    message = models.JSONField()
    metadata = models.JSONField(null=True, blank=True, default=None)

    # The original (pre-canonicalisation) document, kept only when the
    # activity carries an LD signature and may need to be forwarded to a
    # local thread's followers (AP 7.1.2): the signature only verifies
    # over the exact structure the origin signed.
    raw_document = models.JSONField(null=True, blank=True, default=None)

    state = StateField(InboxMessageStates)

    @classmethod
    def create_internal(cls, payload):
        """
        Creates an internal action message
        """
        cls.objects.create(
            message={
                "type": "__internal__",
                "object": payload,
            }
        )

    @property
    def message_type(self):
        return self.message["type"].lower()

    @property
    def message_object_type(self) -> str | None:
        if not isinstance(self.message["object"], dict):
            return None
        # JSON-LD permits multiple types. Prefer a concrete type over the
        # generic ActivityStreams base classes so e.g. ["Document", "Page"]
        # is routed to the Page post handler.
        return get_first_concrete_type(self.message["object"].get("type"))

    @property
    def message_type_full(self):
        if isinstance(self.message.get("object"), dict):
            return f"{self.message_type}.{self.message_object_type}"
        else:
            return f"{self.message_type}"

    @property
    def message_actor(self):
        return self.message.get("actor")

    @property
    def message_object_has_content_or_attachment(self):
        object = self.message.get("object", {})
        return (
            "content" in object
            or "contentMap" in object
            or bool(object.get("attachment"))
        )
