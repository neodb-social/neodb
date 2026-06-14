import re
from typing import TYPE_CHECKING, Iterable

from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger

from .item import Item

if TYPE_CHECKING:
    from django.contrib.auth.models import AnonymousUser

    from users.models import APIdentity, User


class VerifiedCreator(models.Model):
    """A user verified as creator of an item, by matching their fediverse
    identity in the item's external source description, or manually by admin.

    Only rows in VERIFIED state grant edit permission and show up on profile.
    """

    class State(models.TextChoices):
        PENDING = "pending", _("pending")
        VERIFIED = "verified", _("verified")
        FAILED = "failed", _("failed")

    class FailureReason(models.TextChoices):
        NO_FEED = "no_feed", _("no feed url available for this item")
        FETCH_FAILED = "fetch_failed", _("unable to fetch or parse the feed")
        NO_AUDIO = "no_audio", _("no audio episode found in the feed")
        NO_MATCH = (
            "no_match",
            _("no matching identity found in the feed or its linked website"),
        )

    if TYPE_CHECKING:
        item_id: int
        owner_id: int
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="verified_creators"
    )
    owner = models.ForeignKey(
        "users.APIdentity", on_delete=models.CASCADE, related_name="verified_works"
    )
    state = models.CharField(
        max_length=20, choices=State.choices, default=State.PENDING
    )
    matched = models.CharField(max_length=1000, blank=True, default="")
    failure_reason = models.CharField(
        max_length=20, choices=FailureReason.choices, blank=True, default=""
    )
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["item", "owner"]]

    def __str__(self):
        return f"{self.pk}:{self.item_id}:{self.owner_id}:{self.state}"

    @property
    def failure_display(self) -> str:
        return (
            str(self.FailureReason(self.failure_reason).label)
            if self.failure_reason
            else ""
        )


def creator_identity_candidates(user: "User") -> list[str]:
    """Identifiers that prove ownership when found in a feed description."""
    identity = user.identity
    candidates = [f"@{identity.full_handle}"]
    for uri in (identity.actor_uri, identity.profile_uri):
        if uri and uri not in candidates:
            candidates.append(uri)
    if user.mastodon:
        candidates.append(f"@{user.mastodon.handle}")
        if user.mastodon.url:
            candidates.append(user.mastodon.url)
    if user.bluesky and user.bluesky.handle:
        # a bluesky handle is itself a domain, so accept both the @handle
        # form and its url form (e.g. https://name.bsky.social)
        candidates.append(f"@{user.bluesky.handle}")
        if user.bluesky.url:
            candidates.append(user.bluesky.url)
    return candidates


def _mastodon_handle_parts(user: "User | AnonymousUser") -> tuple[str, str] | None:
    """(username, domain) of the user's linked Mastodon handle, lowercased."""
    mastodon = getattr(user, "mastodon", None)
    handle = getattr(mastodon, "handle", None) if mastodon else None
    if not handle:
        return None
    username, _, domain = handle.partition("@")
    if not username or not domain:
        return None
    return username.lower(), domain.lower()


def user_owned_claims_q(user: "User") -> models.Q:
    """Q matching VerifiedCreator rows controlled by ``user``.

    A claim is controlled by the user when its owner is the user's local
    identity, or when the owner is the remote identity of the user's linked
    Mastodon account (matched by the owner's stored username/domain, so no
    remote-actor resolution happens on the request path).
    """
    q = models.Q(owner_id=user.identity.pk)
    parts = _mastodon_handle_parts(user)
    if parts:
        username, domain = parts
        q |= models.Q(
            owner__username__iexact=username, owner__domain_name__iexact=domain
        )
    return q


def user_controls_owner(user: "User | AnonymousUser", owner: "APIdentity") -> bool:
    """Whether ``user`` controls a claim with this (already-loaded) ``owner``.

    Mirrors :func:`user_owned_claims_q` for in-memory checks; compares only
    fields already loaded on ``owner`` (no query, no remote-actor resolution).
    """
    identity = getattr(user, "identity", None)
    if identity is None:
        return False
    if owner.pk == identity.pk:
        return True
    parts = _mastodon_handle_parts(user)
    if parts and owner.username and owner.domain_name:
        username, domain = parts
        return owner.username.lower() == username and (
            owner.domain_name.lower() == domain
        )
    return False


def resolve_creator_identity(user: "User", matched: str) -> "APIdentity":
    """Map a matched candidate string to the fediverse identity it represents.

    A match against the user's linked Mastodon handle/url resolves to that
    remote Mastodon identity (so the verified work is attributed to it); every
    other match (local NeoDB handle/uri, Bluesky) stays the local identity.

    This performs remote-actor resolution (DB lookup, then webfinger fetch) and
    MUST only be called from the background verification task, never on the web
    request path.
    """
    m = matched.strip().lower()
    mastodon = getattr(user, "mastodon", None)
    if mastodon and m:
        mastodon_candidates = {f"@{mastodon.handle}".lower()}
        if mastodon.url:
            mastodon_candidates.add(mastodon.url.lower())
        if m in mastodon_candidates:
            identity = _resolve_remote_identity(mastodon.handle)
            if identity:
                return identity
    return user.identity


def _resolve_remote_identity(handle: str) -> "APIdentity | None":
    """Resolve a ``user@domain`` handle to a remote APIdentity, fetching it via
    webfinger when it is not already known. Returns None on failure."""
    from users.models import APIdentity

    username, _, domain = handle.partition("@")
    if not username or not domain:
        return None
    identity = APIdentity.get_remote(username, domain)
    if identity:
        return identity
    from takahe.models import Identity
    from takahe.utils import Takahe

    try:
        takahe_identity = Identity.by_username_and_domain(username, domain, fetch=True)
    except Exception:
        logger.exception(f"failed to resolve remote identity {handle}")
        return None
    if takahe_identity:
        return Takahe.get_or_create_remote_apidentity(takahe_identity)
    return None


def match_creator_identity(
    descriptions: Iterable[str], candidates: Iterable[str]
) -> str | None:
    """Return the first candidate identifier found in any description.

    Matching is case-insensitive and boundary-guarded on both sides, so that
    e.g. "@a@b.com" matches neither inside "@a@b.com.evil" nor inside
    "@x@a@b.com"; url delimiters on the left keep identifiers embedded in
    other urls (paths, query strings) from matching. A trailing separator
    (e.g. a sentence-ending ".") only blocks the match when it is itself
    followed by another token character, so "follow @a@b.com." still matches.
    """
    texts = [d.lower() for d in descriptions if d]
    for candidate in candidates:
        c = candidate.strip().lower()
        if not c:
            continue
        if "://" in c:
            left, right = r"(?<![\w\-./?#&=])", r"(?![\w@]|[/.\-?#&=]\w)"
        else:
            left, right = r"(?<![\w\-.@/?#&=])", r"(?![\w@]|[.\-]\w)"
        pattern = left + re.escape(c) + right
        if any(re.search(pattern, t) for t in texts):
            return candidate
    return None
