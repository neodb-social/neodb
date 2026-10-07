from collections.abc import Iterable
from urllib.parse import urldefrag, urlparse

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_cache_control
from django.views import View

from activities.models import Post, PostStates
from activities.models.conversation import Conversation
from core.ld import canonicalise
from core.signatures import HttpSignature, VerificationError
from stator.exceptions import TryAgainLater
from users.models import Block, Identity
from users.shortcuts import by_handle_or_404


def signed_fetcher(request: HttpRequest, fetch_domains: set[str]) -> Identity | None:
    """
    The remote actor whose HTTP signature this GET carries, or None.

    A signer we have never seen is fetched only when its domain is in
    fetch_domains, and only for a fragment key id such as Mastodon's
    `https://host/actor#main-key`: a key id like GoToSocial's `.../main-key`
    does not name the actor, and its users are known to us anyway.
    """
    if "signature" not in request.headers:
        return None
    try:
        key_id = HttpSignature.parse_signature(request.headers["signature"])["keyid"]
    except VerificationError, KeyError:
        return None
    actor_uri = urldefrag(key_id).url
    signer = (
        Identity.objects.filter(public_key_id=key_id, local=False).first()
        or Identity.objects.filter(actor_uri=actor_uri, local=False).first()
    )
    if signer is None or not signer.public_key:
        host = (urlparse(actor_uri).hostname or "").lower()
        if actor_uri == key_id or host not in fetch_domains:
            return None
        # transient: nothing is stored unless the actor fetch succeeds
        signer = signer or Identity.by_actor_uri(actor_uri, create=True, transient=True)
        try:
            if not signer.fetch_actor() or not signer.public_key:
                return None
        except TryAgainLater:
            return None
    if signer.local:
        return None
    try:
        HttpSignature.verify_request(request, signer.public_key)
    except VerificationError:
        return None
    return signer.resolved


def private_fetch_allowed(
    signer: Identity, owner: Identity, participants: Iterable[Identity]
) -> bool:
    """
    Whether a signed fetch may read a direct post or conversation.

    Any actor of a server hosting a remote participant qualifies, since that
    server was sent the post anyway; Mastodon signs these fetches with its
    instance actor, not with the participant.
    """
    if signer.domain_id is None or signer.domain.recursively_blocked():
        return False
    if Block.objects.active().filter(source=owner, target=signer, mute=False).exists():
        return False
    for participant in participants:
        if participant.pk == signer.pk:
            return True
        if not participant.local and participant.domain_id == signer.domain_id:
            return True
    return False


def private_json_response(document: dict) -> JsonResponse:
    response = JsonResponse(
        canonicalise(document, include_security=True),
        content_type="application/activity+json",
    )
    # Never let cache_page or a proxy hand this to the next requester
    patch_cache_control(response, private=True, no_store=True)
    return response


def direct_post_for_signed_fetch(
    request: HttpRequest, handle: str, post_id: int
) -> Post | None:
    """
    A local direct post the signer of this request may read, or None.
    """
    identity = by_handle_or_404(request, handle, local=True)
    post = (
        Post.objects.filter(
            author=identity,
            pk=post_id,
            local=True,
            visibility=Post.Visibilities.mentioned,
        )
        .exclude(state__in=[PostStates.deleted, PostStates.deleted_fanned_out])
        .first()
    )
    if post is None:
        return None
    participants = [identity, *post.mentions.all()]
    signer = signed_fetcher(
        request, {p.domain_id for p in participants if not p.local and p.domain_id}
    )
    if signer is None or not private_fetch_allowed(signer, identity, participants):
        return None
    return post


class ConversationCollection(View):
    """
    Serves a conversation URI we minted as an OrderedCollection of its posts,
    to signed fetches from its participants' servers only.
    """

    ITEMS_LIMIT = 200

    def get(self, request, handle, conversation_id):
        if settings.SETUP.NO_FEDERATION:
            return HttpResponse(status=503)
        identity = by_handle_or_404(request, handle, local=True)
        uri = Conversation.local_uri_for(conversation_id, identity.actor_uri)
        conversation = get_object_or_404(Conversation, pk=conversation_id, uri=uri)
        participants = list(conversation.participants.all())
        signer = signed_fetcher(
            request,
            {p.domain_id for p in participants if not p.local and p.domain_id},
        )
        if signer is None or not private_fetch_allowed(signer, identity, participants):
            raise Http404("Unknown conversation")
        posts = conversation.posts.exclude(
            state__in=[PostStates.deleted, PostStates.deleted_fanned_out]
        ).exclude(object_uri__isnull=True)
        items = list(
            posts.order_by("-published").values_list("object_uri", flat=True)[
                : self.ITEMS_LIMIT
            ]
        )
        items.reverse()
        return private_json_response(
            {
                "id": uri,
                "type": "OrderedCollection",
                "attributedTo": identity.actor_uri,
                "totalItems": posts.count(),
                "orderedItems": items,
            }
        )
