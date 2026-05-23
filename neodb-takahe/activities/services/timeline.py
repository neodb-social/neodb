from django.db import models

from activities.models import (
    Hashtag,
    Post,
    PostInteraction,
    PostInteractionStates,
    TimelineEvent,
)
from activities.services import PostService
from users.models import Domain, Identity, List
from users.services import IdentityService


class TimelineService:
    """
    Timelines and stuff!
    """

    def __init__(self, identity: Identity | None):
        self.identity = identity

    @classmethod
    def event_queryset(cls):
        return TimelineEvent.objects.select_related(
            "subject_post",
            "subject_post__author",
            "subject_post__author__domain",
            "subject_identity",
            "subject_identity__domain",
            "subject_post_interaction",
            "subject_post_interaction__identity",
            "subject_post_interaction__identity__domain",
        ).prefetch_related(
            "subject_post__attachments",
            "subject_post__mentions",
            "subject_post__emojis",
        )

    def home(self) -> models.QuerySet[TimelineEvent]:
        exclusive_member_ids = Identity.objects.filter(
            in_lists__identity=self.identity,
            in_lists__exclusive=True,
        ).values("id")
        return (
            self.event_queryset()
            .filter(
                identity=self.identity,
                type__in=[TimelineEvent.Types.post, TimelineEvent.Types.boost],
            )
            .exclude(
                models.Q(
                    type=TimelineEvent.Types.post,
                    subject_post__author_id__in=exclusive_member_ids,
                )
                | models.Q(
                    type=TimelineEvent.Types.boost,
                    subject_identity_id__in=exclusive_member_ids,
                )
            )
            .order_by("-created")
        )

    def local(self, domain: Domain | None = None) -> models.QuerySet[Post]:
        queryset = (
            PostService.queryset()
            .local_public()
            .filter(author__restriction=Identity.Restriction.none)
            .order_by("-id")
        )
        if self.identity is not None:
            queryset = queryset.filter(author__domain=self.identity.domain).visible_to(
                self.identity, include_replies=True
            )
        elif domain is not None:
            queryset = queryset.filter(author__domain=domain)
        return queryset

    def federated(self) -> models.QuerySet[Post]:
        return (
            PostService.queryset(exclude_threshold=7)
            .public()
            .visible_to(self.identity)
            .filter(author__restriction=Identity.Restriction.none)
            .order_by("-id")
        )

    def hashtag(self, hashtag: str | Hashtag) -> models.QuerySet[Post]:
        return (
            PostService.queryset(exclude_threshold=7)
            .public()
            .visible_to(self.identity)
            .filter(author__restriction=Identity.Restriction.none)
            .tagged_with(hashtag)
            # .order_by("-id")  # NeoDB: disabled due to performance
        )

    def notifications(self, types: list[str]) -> models.QuerySet[TimelineEvent]:
        filter_types = set(types)
        notify_ids: list[int] = []
        if "post" in types:
            # If post notifications are requested, only show from accounts we're
            # following with `notify=True` set. Materialize the IDs so the main
            # query stays a flat IN list instead of becoming a correlated
            # OR-with-subquery, which the planner can't combine with the
            # existing TimelineEvent indexes.
            filter_types.discard("post")
            notify_ids = list(
                self.identity.outbound_follows.active()
                .filter(notify=True)
                .values_list("target_id", flat=True)
            )
        if notify_ids:
            q = models.Q(type__in=filter_types) | (
                models.Q(type="post") & models.Q(subject_identity_id__in=notify_ids)
            )
        else:
            q = models.Q(type__in=filter_types)
        return (
            self.event_queryset()
            .filter(q, identity=self.identity, dismissed=False)
            .order_by("-created")
        )

    def identity_public(
        self,
        identity: Identity,
        include_boosts: bool = True,
        include_replies: bool = True,
    ):
        """
        Returns timeline events with all of an identity's publicly visible posts
        and their boosts
        """
        filter = models.Q(
            type=TimelineEvent.Types.post,
            subject_post__author=identity,
            subject_post__visibility__in=[
                Post.Visibilities.public,
                Post.Visibilities.local_only,
                Post.Visibilities.unlisted,
            ],
        )
        if include_boosts:
            filter = filter | models.Q(
                type=TimelineEvent.Types.boost, subject_identity=identity
            )
        if not include_replies:
            filter = filter & models.Q(subject_post__in_reply_to__isnull=True)
        return (
            self.event_queryset()
            .filter(
                filter,
                identity=identity,
            )
            .order_by("-created")
        )

    def identity_pinned(self) -> models.QuerySet[Post]:
        """
        Return all pinned posts that are publicly visible for an identity
        """
        return (
            PostService.queryset()
            .public()
            .filter(
                interactions__identity=self.identity,
                interactions__type=PostInteraction.Types.pin,
                interactions__state__in=PostInteractionStates.group_active(),
            )
            .visible_to(
                self.identity,
                include_replies=True,
                include_muted=True,
            )
        )

    def likes(self) -> models.QuerySet[Post]:
        """
        Return all liked posts for an identity
        """
        return (
            PostService.queryset()
            .filter(
                interactions__identity=self.identity,
                interactions__type=PostInteraction.Types.like,
                interactions__state__in=PostInteractionStates.group_active(),
            )
            .order_by("-id")
        )

    def conversations(self) -> models.QuerySet:
        """Return conversations for the current identity, excluding dismissed ones."""
        from activities.models.conversation import Conversation

        return (
            Conversation.objects.filter(
                memberships__identity=self.identity,
                memberships__dismissed=False,
            )
            .select_related(
                "last_post",
                "last_post__author",
                "last_post__author__domain",
            )
            .prefetch_related(
                "participants",
                "participants__domain",
                "last_post__attachments",
                "last_post__mentions",
                "last_post__mentions__domain",
                "last_post__emojis",
            )
            .order_by("-id")
        )

    def bookmarks(self) -> models.QuerySet[Post]:
        """
        Return all bookmarked posts for an identity
        """
        return (
            PostService.queryset()
            .filter(bookmarks__identity=self.identity)
            .order_by("-id")
        )

    def for_list(self, alist: List) -> models.QuerySet[Post]:
        """
        Return posts from members of `alist`, filtered by the lists replies policy.
        """
        assert self.identity  # Appease mypy
        # We only need to include this if we need to filter on it.
        include_author = alist.replies_policy == "followed"
        members = alist.members.all()
        queryset = PostService.queryset(
            include_reply_to_author=include_author
        ).visible_to(
            self.identity,
            include_replies=True,
            include_muted=True,  # Twitter like behavior
        )
        match alist.replies_policy:
            case "none":
                # Don't show any replies, just original posts from list members.
                criteria = models.Q(author__in=members) & models.Q(
                    in_reply_to__isnull=True
                )
            case "followed":
                # Show posts from list members OR from accounts you follow replying to
                # posts by list members.
                criteria = models.Q(author__in=members) | (
                    models.Q(author__in=IdentityService(self.identity).following())
                    & models.Q(in_reply_to_author_id__in=members)
                )
            case _:
                # The default is to show posts (and replies) from list members.
                criteria = models.Q(author__in=members)
        return queryset.filter(criteria).order_by("-id")
