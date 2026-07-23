import base64
import json
import logging
from urllib.parse import urldefrag, urlparse

from activities.models import Post
from activities.services import TimelineService
from core import sentry
from core.decorators import cache_page
from core.ld import canonicalise, get_str_or_id
from core.signatures import (
    HttpSignature,
    LDSignature,
    VerificationError,
    VerificationFormatError,
)
from core.views import StaticContentView
from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from takahe.neodb import __version__ as __neodb_version__

from users.models import Identity, InboxMessage, SystemActor
from users.models.domain import Domain
from users.shortcuts import by_handle_or_404

logger = logging.getLogger(__name__)


class HttpResponseUnauthorized(HttpResponse):
    status_code = 401


class FederatedView(View):
    """
    Base class for all views requires federation
    """

    def dispatch(self, request, *args, **kwargs):
        if settings.SETUP.NO_FEDERATION:
            return HttpResponse(status=503)
        return super().dispatch(request, *args, **kwargs)


class FederatedStaticView(StaticContentView):
    """
    Base class for all static views requires federation
    """

    def dispatch(self, request, *args, **kwargs):
        if settings.SETUP.NO_FEDERATION:
            return HttpResponse(status=503)
        return super().dispatch(request, *args, **kwargs)


class HostMeta(View):
    """
    Returns a canned host-meta response
    """

    def get(self, request):
        return HttpResponse(
            """<?xml version="1.0" encoding="UTF-8"?>
            <XRD xmlns="http://docs.oasis-open.org/ns/xri/xrd-1.0">
            <Link rel="lrdd" template="https://%s/.well-known/webfinger?resource={uri}"/>
            </XRD>"""
            % request.headers["host"],
            content_type="application/xrd+xml",
        )


class NodeInfo(View):
    """
    Returns the well-known nodeinfo response, pointing to the 2.0 one
    """

    def get(self, request):
        host = request.META.get("HOST", settings.MAIN_DOMAIN)
        return JsonResponse(
            {
                "links": [
                    {
                        "rel": "http://nodeinfo.diaspora.software/ns/schema/2.0",
                        "href": f"https://{host}/nodeinfo/2.0/",
                    }
                ]
            }
        )


@method_decorator(cache_page(), name="dispatch")
class NodeInfo2(View):
    """
    Returns the nodeinfo 2.0 response
    """

    def get(self, request):
        # Fetch some user stats
        if request.domain:
            local_identities = Identity.objects.filter(
                local=True, domain=request.domain
            ).count()
            local_posts = Post.objects.filter(
                local=True, author__domain=request.domain
            ).count()
            metadata = {
                "nodeName": request.config.site_name,
                "features": ["quote_posting", "editing", "polls"],
            }
        else:
            local_identities = Identity.objects.filter(local=True).count()
            local_posts = Post.objects.filter(local=True).count()
            metadata = {"features": ["quote_posting", "editing", "polls"]}
        if settings.SETUP.NO_FEDERATION:
            metadata["federation"] = {"enabled": False}
        return JsonResponse(
            {
                "version": "2.0",
                "software": {"name": "neodb", "version": __neodb_version__},
                "protocols": ["activitypub", "neodb"],
                "services": {"outbound": [], "inbound": []},
                "usage": {
                    "users": {"total": local_identities},
                    "localPosts": local_posts,
                },
                "openRegistrations": request.config.signup_allowed,
                "metadata": metadata,
            }
        )


@method_decorator(cache_page(), name="dispatch")
class Webfinger(FederatedView):
    """
    Services webfinger requests
    """

    def get(self, request):
        resource = request.GET.get("resource")
        if not resource:
            return HttpResponseBadRequest("No resource specified")
        if not resource.startswith("acct:"):
            return HttpResponseBadRequest("Not an account resource")
        handle = resource[5:]

        if handle.startswith("__system__@"):
            # They are trying to webfinger the system actor
            actor = SystemActor()
        else:
            actor = by_handle_or_404(request, handle)

        return JsonResponse(actor.to_webfinger(), content_type="application/jrd+json")


@method_decorator(csrf_exempt, name="dispatch")
class Inbox(FederatedView):
    """
    AP Inbox endpoint
    """

    def post(self, request, handle=None):
        sentry.count("ap.message.received")
        # Reject bodies that are unfeasibly big
        if len(request.body) > settings.JSONLD_MAX_SIZE:
            return HttpResponseBadRequest("Payload size too large")
        # Load the LD. Keep the raw parsed JSON separately: LD signatures are
        # computed over the original document structure, so verification must
        # use raw_document rather than the canonicalised form.
        try:
            raw_document = json.loads(request.body)
            document = canonicalise(raw_document, include_security=True, outbound=False)
        except ValueError:
            logger.warning(
                "Inbox error when parsing JSON to LDDocument: %s", request.body.decode()
            )
            return HttpResponseBadRequest("Error parsing JSON")
        document_type = document["type"]
        document_subtype = None
        if isinstance(document.get("object"), dict):
            document_subtype = document["object"].get("type")

        # Find the Identity by the actor on the incoming item
        # This ensures that the signature used for the headers matches the actor
        # described in the payload.
        if "actor" not in document:
            logger.warning("Inbox error: unspecified actor")
            return HttpResponseBadRequest("Unspecified actor")

        identity = Identity.by_actor_uri(document["actor"], create=True, transient=True)
        if (
            document_type == "Delete"
            and document["actor"] == document["object"]
            and identity._state.adding
        ):
            # We don't have an Identity record for the user. No-op
            return HttpResponse(status=202)

        # See if it's from a blocked user or domain - without calling
        # fetch_actor, which would fetch data from potentially bad actor
        domain = identity.domain
        if not domain:
            actor_url_parts = urlparse(document["actor"])
            domain = Domain.get_remote_domain(actor_url_parts.hostname)
        if identity.blocked or domain.recursively_blocked():
            # I love to lie! Throw it away!
            logger.info(
                "Inbox: Discarded message from blocked %s %s",
                "domain" if domain.recursively_blocked() else "user",
                identity.actor_uri,
            )
            return HttpResponse(status=202)

        # See if it's a type of message we know we want to ignore right now
        # (Lemmy-style group-announced votes and their undos, which we
        # don't tally). Announced Create/Update/Delete activities are let
        # through so group-relayed content (posts, comments, edits) can be
        # ingested from its origin.
        if document_type == "Announce" and document_subtype in [
            "Like",
            "Dislike",
            "Undo",
        ]:
            return HttpResponse(status=202)

        http_sig_present = "Signature" in request.headers
        ld_sig_present = "signature" in document
        verified = False
        ld_sig_verified = False  # True when the LD signature itself checked out
        relay_mode = False  # True when HTTP signer != document actor
        relay_http_verified = False  # True when relay HTTP sig verified immediately
        metadata = {}

        # Authenticate HTTP signature if present. Parse keyId first to detect
        # relay deliveries (where the HTTP signer differs from document["actor"]).
        # An invalid signature is a hard rejection. For unknown signers without a
        # cached key, pre-compute data for deferred verification.
        if http_sig_present:
            try:
                signature_details = HttpSignature.parse_signature(
                    request.headers["signature"]
                )
            except VerificationFormatError as e:
                logger.warning("Inbox error: Bad HTTP signature format: %s", e.args[0])
                return HttpResponseBadRequest(e.args[0])

            key_id_actor = urldefrag(signature_details["keyid"]).url
            relay_mode = key_id_actor != document["actor"]
            signer_identity = (
                Identity.by_actor_uri(key_id_actor, create=True, transient=True)
                if relay_mode
                else identity
            )

            try:
                if signer_identity.public_key:
                    HttpSignature.verify_request(request, signer_identity.public_key)
                    if relay_mode:
                        relay_http_verified = True
                        logger.debug(
                            "Inbox: relay HTTP sig from %s ok for %s",
                            signer_identity,
                            identity,
                        )
                    else:
                        verified = True
                        logger.debug(
                            "Inbox: %s from %s has good HTTP signature",
                            document_type,
                            identity,
                        )
                else:
                    logger.info("Inbox: No key available for %s", key_id_actor)
                    # Pre-compute signed cleartext for deferred verification.
                    if "digest" in request.headers:
                        expected_digest = HttpSignature.calculate_digest(request.body)
                        if request.headers["digest"] != expected_digest:
                            return HttpResponseBadRequest("Digest is incorrect")
                    headers_string = HttpSignature.headers_from_request(
                        request, signature_details["headers"], signature_details
                    )
                    sig_b64 = base64.b64encode(signature_details["signature"]).decode()
                    if relay_mode:
                        metadata["relay_http_sig"] = {
                            "relay_uri": key_id_actor,
                            "signature": sig_b64,
                            "headers_string": headers_string,
                        }
                    else:
                        metadata["http_sig"] = {
                            "actor_uri": document["actor"],
                            "signature": sig_b64,
                            "headers_string": headers_string,
                        }
            except VerificationFormatError as e:
                logger.warning("Inbox error: Bad HTTP signature format: %s", e.args[0])
                return HttpResponseBadRequest(e.args[0])
            except VerificationError:
                logger.warning(
                    "Inbox error: Bad HTTP signature from %s", signer_identity
                )
                # TODO: stale-key retry missing. This method only fetches when the key
                # is absent. If the key IS present but stale (remote actor rotated keys
                # after we cached them), deferred verification will fail permanently.
                return HttpResponseUnauthorized("Bad signature")

            # Relay deliveries must carry an LD signature from the document actor
            # so the true author can be authenticated independently of the relay.
            if relay_mode and not ld_sig_present:
                logger.warning(
                    "Inbox error: Relay from %s missing LD signature", signer_identity
                )
                return HttpResponseUnauthorized("Relay requires LD signature")

        # Validate LD Signature when HTTP sig did not already verify the message
        # (direct delivery), or always for relay where LD sig authenticates the
        # document actor independently of the relay.
        # Note: direct delivery with valid HTTP sig skips this block entirely —
        # matching Mastodon's behaviour and avoiding pyld/ruby-jsonld URDNA2015
        # interop failures.
        # https://docs.joinmastodon.org/spec/security/#ld
        if ld_sig_present and not verified:
            try:
                creator = urldefrag(document["signature"]["creator"]).url
            except KeyError, TypeError:
                logger.warning("Inbox error: Malformed LD signature block")
                return HttpResponseBadRequest("Malformed LD signature")
            if creator != document["actor"]:
                logger.warning(
                    "Inbox error: LD signature creator %s does not match actor %s",
                    creator,
                    document["actor"],
                )
                return HttpResponseUnauthorized(
                    "Signature creator does not match actor"
                )
            try:
                creator_identity = Identity.by_actor_uri(
                    creator, create=True, transient=True
                )
                if creator_identity.public_key:
                    # Verify against raw_document (original structure as signed),
                    # not the canonicalized form which may differ in N-Quads output.
                    LDSignature.verify_signature(
                        raw_document, creator_identity.public_key
                    )
                    ld_sig_verified = True
                    # For relay: only mark fully verified when relay HTTP was also
                    # confirmed immediately (not deferred).
                    if not relay_mode or relay_http_verified:
                        verified = True
                    logger.debug(
                        "Inbox: %s from %s has good LD signature",
                        document["type"],
                        creator_identity,
                    )
                else:
                    logger.info("Inbox: New actor, no key available: %s", creator)
                    # Store raw_document so deferred verification can also use the
                    # original structure rather than the canonicalized message.
                    metadata["ld_sig"] = {
                        "creator_uri": creator,
                        "raw_document": raw_document,
                    }
            except VerificationFormatError as e:
                logger.warning("Inbox error: Bad LD signature format: %s", e.args[0])
                return HttpResponseBadRequest(e.args[0])
            except VerificationError:
                logger.warning(
                    "Inbox error: Bad LD signature from %s %s",
                    creator_identity,
                    document.get("id"),
                )
                return HttpResponseUnauthorized("Bad signature")

        if not (http_sig_present or ld_sig_present):
            logger.warning(
                "Inbox: %s from %s has no signature, rejecting.",
                document["type"],
                identity,
            )
            return HttpResponseUnauthorized("No signature")

        # Don't allow injection of internal messages
        if document["type"].startswith("__"):
            return HttpResponseUnauthorized("Bad type")

        # Keep the original document when the activity is LD-signed and
        # concerns a reply to a LOCAL thread: forwarding it to the thread's
        # followers (AP 7.1.2) must re-send the exact structure the author
        # signed. The locality pre-check keeps storage and signature
        # verification off the hot path for the (vast) majority of inbound
        # replies that have nothing to do with our threads.
        forward_raw_document = None
        if ld_sig_present and document_type in ["Create", "Update", "Delete"]:
            keep = False
            obj = document.get("object")
            if document_type == "Delete":
                deleted_uri = obj if isinstance(obj, str) else (obj or {}).get("id")
                parent_uri = (
                    Post.objects.filter(object_uri=deleted_uri, local=False)
                    .exclude(in_reply_to__isnull=True)
                    .exclude(in_reply_to="")
                    .values_list("in_reply_to", flat=True)
                    .first()
                    if deleted_uri
                    else None
                )
            else:
                parent_uri = (
                    get_str_or_id(obj.get("inReplyTo"))
                    if isinstance(obj, dict)
                    else None
                )
            if parent_uri:
                keep = Post.objects.filter(object_uri=parent_uri, local=True).exists()
            # A valid HTTP signature skips LD verification above, but we
            # must not amplify a document whose LD signature would fail at
            # the receiving end: verify it before agreeing to forward.
            if keep and not ld_sig_verified:
                try:
                    creator = urldefrag(document["signature"]["creator"]).url
                    if creator == document["actor"] and identity.public_key:
                        LDSignature.verify_signature(raw_document, identity.public_key)
                        ld_sig_verified = True
                except KeyError, TypeError, VerificationError, VerificationFormatError:
                    logger.info(
                        "Inbox: Unverifiable LD signature, not forwarding %s",
                        document.get("id"),
                    )
            if keep and ld_sig_verified:
                # Re-parse the body: canonicalise mutates raw_document
                # (e.g. appending to @context) and forwarding must resend
                # exactly what the origin signed.
                forward_raw_document = json.loads(request.body)

        if verified:
            InboxMessage.objects.create(
                message=document, raw_document=forward_raw_document
            )
        else:
            # Signatures present but keys unavailable; defer until
            # the actor's key can be fetched and the signature verified.
            logger.info(
                "Inbox: Deferring %s from %s pending key fetch",
                document["type"],
                identity,
            )
            InboxMessage.objects.create(
                message=document,
                metadata=metadata,
                raw_document=forward_raw_document,
            )
        sentry.count("ap.message.enqueued", attributes={"type": document_type.lower()})
        return HttpResponse(status=202)


class Outbox(FederatedView):
    """
    The ActivityPub outbox for an identity
    """

    def get(self, request, handle):
        self.identity = by_handle_or_404(
            self.request,
            handle,
            local=False,
            fetch=True,
        )
        # If this not a local actor, 404
        if not self.identity.local:
            raise Http404("Not a local identity")
        # Return an ordered collection with the most recent 10 public posts
        posts = list(self.identity.posts.not_hidden().public()[:10])
        return JsonResponse(
            canonicalise(
                {
                    "type": "OrderedCollection",
                    "totalItems": len(posts),
                    "orderedItems": [post.to_ap() for post in posts],
                }
            ),
            content_type="application/activity+json",
        )


class FeaturedCollection(FederatedView):
    """
    An ordered collection of all pinned posts of an identity
    """

    def get(self, request, handle):
        self.identity = by_handle_or_404(
            request,
            handle,
            local=False,
            fetch=True,
        )
        if not self.identity.local:
            raise Http404("Not a local identity")
        posts = list(TimelineService(self.identity).identity_pinned())
        return JsonResponse(
            canonicalise(
                {
                    "type": "OrderedCollection",
                    "id": self.identity.actor_uri + "collections/featured/",
                    "totalItems": len(posts),
                    "orderedItems": [post.to_ap() for post in posts],
                }
            ),
            content_type="application/activity+json",
        )


class FeaturedTags(FederatedView):
    """
    An ordered collection of all pinned hashtags of an identity
    """

    def get(self, request, handle):
        self.identity = by_handle_or_404(
            request,
            handle,
            local=False,
            fetch=True,
        )
        if not self.identity.local:
            raise Http404("Not a local identity")
        tags = list(self.identity.hashtag_features.select_related("hashtag"))
        return JsonResponse(
            canonicalise(
                {
                    "type": "OrderedCollection",
                    "id": self.identity.actor_uri + "collections/tags/",
                    "totalItems": len(tags),
                    "orderedItems": [
                        tag.hashtag.to_ap(domain=request.domain) for tag in tags
                    ],
                }
            ),
            content_type="application/activity+json",
        )


@method_decorator(cache_control(max_age=60 * 15), name="dispatch")
class EmptyOutbox(FederatedStaticView):
    """
    A fixed-empty outbox for the system actor
    """

    content_type: str = "application/activity+json"

    def get_static_content(self) -> str | bytes:
        return json.dumps(
            canonicalise(
                {
                    "type": "OrderedCollection",
                    "totalItems": 0,
                    "orderedItems": [],
                }
            )
        )


@method_decorator(cache_control(max_age=60 * 15), name="dispatch")
class SystemActorView(FederatedStaticView):
    """
    Special endpoint for the overall system actor
    """

    content_type: str = "application/activity+json"

    def get_static_content(self) -> str | bytes:
        return json.dumps(
            canonicalise(
                SystemActor().to_ap(),
                include_security=True,
            )
        )
