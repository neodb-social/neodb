import io
from datetime import timedelta
from typing import TYPE_CHECKING

import blurhash
from django.conf import settings
from django.core.cache import cache
from django.core.files.images import ImageFile
from django.core.signing import b62_encode
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from loguru import logger
from PIL import Image

from .models import *

if TYPE_CHECKING:
    from users.models import User as NeoUser


class Takahe:
    Visibilities = Post.Visibilities

    @staticmethod
    def get_domain():
        domain = settings.SITE_INFO["site_domain"]
        d = Domain.objects.filter(domain=domain).first()
        if not d:
            logger.info(f"Creating takahe domain {domain}")
            d = Domain.objects.create(
                domain=domain,
                local=True,
                service_domain=None,
                notes="NeoDB",
                nodeinfo=None,
            )
        return d

    @staticmethod
    def get_node_name_for_domain(d: str):
        domain = Domain.objects.filter(domain=d).first()
        if domain and domain.nodeinfo:
            return domain.nodeinfo.get("metadata", {}).get("nodeName")

    @staticmethod
    def sync_password(u: "NeoUser"):
        user = User.objects.filter(pk=u.pk).first()
        if not user:
            raise ValueError(f"Cannot find takahe user {u}")
        elif user.password != u.password:
            logger.info(f"Updating takahe user {u} password")
            user.password = u.password
            user.save()

    @staticmethod
    def init_identity_for_local_user(u: "NeoUser"):
        """
        When a new local NeoDB user is created,
        create a takahe user with the NeoDB user pk,
        create a takahe identity,
        then create a NeoDB APIdentity with the takahe identity pk.
        """
        from users.models import APIdentity

        logger.info(f"User {u} initialize identity")
        if not u.username:
            logger.warning(f"User {u} has no username")
            return None
        with transaction.atomic(using="takahe"):
            user = User.objects.filter(pk=u.pk).first()
            handler = "@" + u.username
            if not user:
                logger.info(f"Creating takahe user {u}")
                user = User.objects.create(pk=u.pk, email=handler, password=u.password)
            else:
                if user.email != handler:
                    logger.warning(f"Updating takahe user {u} email to {handler}")
                    user.email = handler
                    user.save()
            domain = Domain.objects.get(domain=settings.SITE_INFO["site_domain"])
            identity = Identity.objects.filter(username=u.username, local=True).first()
            if not identity:
                logger.info(f"Creating takahe identity {u}@{domain}")
                identity = Identity.objects.create(
                    actor_uri=f"https://{domain.uri_domain}/@{u.username}@{domain.domain}/",
                    profile_uri=u.absolute_url,
                    username=u.username,
                    domain=domain,
                    name=u.username,
                    local=True,
                    discoverable=True,
                )
            if not identity.private_key and not identity.public_key:
                identity.generate_keypair()
                identity.ensure_uris()
            if not user.identities.filter(pk=identity.pk).exists():
                user.identities.add(identity)
            apidentity = APIdentity.objects.filter(pk=identity.pk).first()
            if not apidentity:
                logger.info(f"Creating APIdentity for {identity}")
                apidentity = APIdentity.objects.create(
                    user=u,
                    id=identity.pk,
                    local=True,
                    username=u.username,
                    domain_name=domain.domain,
                    deleted=identity.deleted,
                )
            elif apidentity.username != identity.username:
                logger.warning(
                    f"Updating APIdentity {apidentity} username to {identity.username}"
                )
                apidentity.username = identity.username
                apidentity.save()
            if u.identity != apidentity:
                logger.warning(f"Linking user {u} identity to {apidentity}")
                u.identity = apidentity
                u.save(update_fields=["identity"])
            return apidentity

    @staticmethod
    def get_identity_by_handler(username: str, domain: str) -> Identity | None:
        return Identity.objects.filter(
            username__iexact=username, domain__domain__iexact=domain
        ).first()

    @staticmethod
    def request_delete_identity(identity_pk: int):
        i = Identity.objects.filter(pk=identity_pk).first()
        if i:
            InboxMessage.create_internal(
                {"type": "DeleteIdentity", "actor": i.actor_uri}
            )
            logger.warning(f"Identity {i} deletion requested")
            return True
        else:
            logger.error(f"Identity {i} not found")
            return False

    @staticmethod
    def create_internal_message(message: dict):
        InboxMessage.create_internal(message)

    @staticmethod
    def fetch_remote_identity(handler: str) -> int | None:
        d = handler.split("@")[-1]
        domain = Domain.objects.filter(domain=d).first()
        if domain and domain.recursively_blocked():
            return
        InboxMessage.create_internal({"type": "FetchIdentity", "handle": handler})

    @staticmethod
    def get_identity(pk: int):
        return Identity.objects.get(pk=pk)

    @staticmethod
    def get_identity_by_local_user(u: "NeoUser"):
        return (
            Identity.objects.filter(pk=u.identity.pk, local=True).first()
            if u and u.is_authenticated and u.identity
            else None
        )

    @staticmethod
    def get_or_create_remote_apidentity(identity: Identity):
        from users.models import APIdentity

        apid = APIdentity.objects.filter(pk=identity.pk).first()
        if not apid:
            if identity.local:
                raise ValueError(f"local takahe identity {identity} missing APIdentity")
            if not identity.domain_id:
                raise ValueError(f"remote takahe identity {identity} missing domain")
            apid = APIdentity.objects.get_or_create(
                id=identity.pk,
                defaults={
                    "user": None,
                    "local": False,
                    "username": identity.username,
                    "domain_name": identity.domain_id,
                    "deleted": identity.deleted,
                    "anonymous_viewable": False,
                },
            )[0]
        return apid

    @staticmethod
    def get_local_user_by_identity(identity: Identity):
        from users.models import User as NeoUser

        return NeoUser.objects.get(identity_id=identity.pk) if identity.local else None

    @staticmethod
    def get_is_following(identity_pk: int, target_pk: int):
        return Follow.objects.filter(
            source_id=identity_pk, target_id=target_pk, state="accepted"
        ).exists()

    @staticmethod
    def get_is_follow_requesting(identity_pk: int, target_pk: int):
        return Follow.objects.filter(
            source_id=identity_pk,
            target_id=target_pk,
            state__in=["unrequested", "pending_approval"],
        ).exists()

    @staticmethod
    def get_is_muting(identity_pk: int, target_pk: int):
        return Block.objects.filter(
            source_id=identity_pk,
            target_id=target_pk,
            state__in=["new", "sent", "awaiting_expiry"],
            mute=True,
        ).exists()

    @staticmethod
    def get_is_blocking(identity_pk: int, target_pk: int):
        return Block.objects.filter(
            source_id=identity_pk,
            target_id=target_pk,
            state__in=["new", "sent", "awaiting_expiry"],
            mute=False,
        ).exists()

    @staticmethod
    def get_following_ids(identity_pk: int):
        targets = Follow.objects.filter(
            source_id=identity_pk, state="accepted"
        ).values_list("target", flat=True)
        return list(targets)

    @staticmethod
    def get_follower_ids(identity_pk: int):
        targets = Follow.objects.filter(
            target_id=identity_pk, state="accepted"
        ).values_list("source", flat=True)
        return list(targets)

    @staticmethod
    def get_following_request_ids(identity_pk: int):
        targets = Follow.objects.filter(
            source_id=identity_pk, state__in=["unrequested", "pending_approval"]
        ).values_list("target", flat=True)
        return list(targets)

    @staticmethod
    def get_requested_follower_ids(identity_pk: int):
        targets = Follow.objects.filter(
            target_id=identity_pk, state="pending_approval"
        ).values_list("source", flat=True)
        return list(targets)

    @staticmethod
    def update_follow_state(
        source_pk: int, target_pk: int, from_states: list[str], to_state: str
    ):
        follow = Follow.objects.filter(source_id=source_pk, target_id=target_pk).first()
        if (
            follow
            and (not from_states or follow.state in from_states)
            and follow.state != to_state
        ):
            Takahe.update_state(follow, to_state)
        return follow

    @staticmethod
    def follow(source_pk: int, target_pk: int, force_accept: bool = False):
        try:
            follow = Follow.objects.get(source_id=source_pk, target_id=target_pk)
            if follow.state != "accepted":
                Takahe.update_state(
                    follow, "accepted" if force_accept else "unrequested"
                )
        except Follow.DoesNotExist:
            source = Identity.objects.get(pk=source_pk)
            follow = Follow.objects.create(
                source_id=source_pk,
                target_id=target_pk,
                boosts=True,
                uri="",
                state="accepted" if force_accept else "unrequested",
            )
            follow.uri = source.actor_uri + f"follow/{follow.pk}/"
            follow.save()

    @staticmethod
    def unfollow(source_pk: int, target_pk: int):
        Takahe.update_follow_state(source_pk, target_pk, [], "undone")
        InboxMessage.create_internal(
            {
                "type": "ClearTimeline",
                "object": target_pk,
                "actor": source_pk,
            }
        )

    @staticmethod
    def accept_follow_request(source_pk: int, target_pk: int):
        Takahe.update_follow_state(source_pk, target_pk, [], "accepting")

    @staticmethod
    def reject_follow_request(source_pk: int, target_pk: int):
        Takahe.update_follow_state(source_pk, target_pk, [], "rejecting")

    @staticmethod
    def get_muting_ids(identity_pk: int) -> list[int]:
        targets = Block.objects.filter(
            source_id=identity_pk,
            mute=True,
            state__in=["new", "sent", "awaiting_expiry"],
        ).values_list("target", flat=True)
        return list(targets)

    @staticmethod
    def get_blocking_ids(identity_pk: int) -> list[int]:
        targets = Block.objects.filter(
            source_id=identity_pk,
            mute=False,
            state__in=["new", "sent", "awaiting_expiry"],
        ).values_list("target", flat=True)
        return list(targets)

    @staticmethod
    def get_rejecting_ids(identity_pk: int) -> list[int]:
        pks1 = Block.objects.filter(
            source_id=identity_pk,
            mute=False,
            state__in=["new", "sent", "awaiting_expiry"],
        ).values_list("target", flat=True)
        pks2 = Block.objects.filter(
            target_id=identity_pk,
            mute=False,
            state__in=["new", "sent", "awaiting_expiry"],
        ).values_list("source", flat=True)
        return list(set(list(pks1) + list(pks2)))

    @staticmethod
    def block_or_mute(source_pk: int, target_pk: int, is_mute: bool):
        source = Identity.objects.get(pk=source_pk)
        if not source.local:
            raise ValueError(f"Cannot block/mute from remote identity {source}")
        with transaction.atomic():
            block, _ = Block.objects.update_or_create(
                defaults={"state": "new"},
                source_id=source_pk,
                target_id=target_pk,
                mute=is_mute,
            )
            if block.state != "new" or not block.uri:
                block.state = "new"
                block.uri = source.actor_uri + f"block/{block.pk}/"
                block.save()
            if not is_mute:
                Takahe.unfollow(source_pk, target_pk)
                Takahe.reject_follow_request(target_pk, source_pk)
                InboxMessage.create_internal(
                    {
                        "type": "ClearTimeline",
                        "object": target_pk,
                        "actor": source_pk,
                        "fullErase": True,
                    }
                )
            return block

    @staticmethod
    def undo_block_or_mute(source_pk: int, target_pk: int, is_mute: bool):
        Block.objects.filter(
            source_id=source_pk, target_id=target_pk, mute=is_mute
        ).update(state="undone")

    @staticmethod
    def block(source_pk: int, target_pk: int):
        return Takahe.block_or_mute(source_pk, target_pk, False)

    @staticmethod
    def unblock(source_pk: int, target_pk: int):
        return Takahe.undo_block_or_mute(source_pk, target_pk, False)

    @staticmethod
    def mute(source_pk: int, target_pk: int):
        return Takahe.block_or_mute(source_pk, target_pk, True)

    @staticmethod
    def unmute(source_pk: int, target_pk: int):
        return Takahe.undo_block_or_mute(source_pk, target_pk, True)

    @staticmethod
    def _force_state_cycle():  # for unit testing only
        Follow.objects.filter(
            state__in=["rejecting", "undone", "pending_removal"]
        ).delete()
        Follow.objects.all().update(state="accepted")
        Block.objects.filter(state="new").update(state="sent")
        Block.objects.exclude(state="sent").delete()

    @staticmethod
    def upload_image(
        author_pk: int,
        filename: str,
        content: bytes,
        mimetype: str,
        description: str = "",
    ) -> PostAttachment:
        if len(content) > 1024 * 1024 * 5:
            raise ValueError("Image too large")
        main_file = ImageFile(io.BytesIO(content), name=filename)
        resized_image = Image.open(io.BytesIO(content))
        resized_image.thumbnail((400, 225), resample=Image.Resampling.BILINEAR)
        new_image_bytes = io.BytesIO()
        resized_image.save(new_image_bytes, format="webp", save_all=True)
        thumbnail_file = ImageFile(new_image_bytes, name="image.webp")
        hash = blurhash.encode(resized_image, 4, 4)
        attachment = PostAttachment.objects.create(
            mimetype=mimetype,
            width=main_file.width,
            height=main_file.height,
            name=description or None,
            state="fetched",
            author_id=author_pk,
            file=main_file,
            thumbnail=thumbnail_file,
            blurhash=hash,
        )
        attachment.save()
        return attachment

    @staticmethod
    def post(
        author_pk: int,
        content: str,
        visibility: Visibilities,
        prepend_content: str = "",
        append_content: str = "",
        summary: str | None = None,
        sensitive: bool = False,
        data: dict | None = None,
        post_pk: int | None = None,
        post_time: datetime.datetime | None = None,
        edit_time: datetime.datetime | None = None,
        reply_to_pk: int | None = None,
        attachments: list | None = None,
        language: str = "",
    ) -> Post | None:
        identity = Identity.objects.get(pk=author_pk)
        post = (
            Post.objects.filter(author=identity, pk=post_pk).first()
            if post_pk
            else None
        )
        if post_pk and not post:
            raise ValueError(f"Cannot find post to edit: {post_pk}")
        reply_to_post = (
            Post.objects.filter(pk=reply_to_pk).first() if reply_to_pk else None
        )
        if reply_to_pk and not reply_to_post:
            raise ValueError(f"Cannot find post to reply: {reply_to_pk}")
        if post:
            post.edit_local(
                content,
                prepend_content,
                append_content,
                summary,
                sensitive,
                visibility=visibility,
                type_data=data,
                published=post_time,
                edited=edit_time,
                attachments=attachments,
                language=language,
            )
        else:
            post = Post.create_local(
                identity,
                content,
                prepend_content,
                append_content,
                summary,
                sensitive,
                visibility=visibility,
                type_data=data,
                published=post_time,
                edited=None,
                reply_to=reply_to_post,
                attachments=attachments,
                language=language,
            )
            TimelineEvent.objects.get_or_create(
                identity=identity,
                type="post",
                subject_post=post,
                subject_identity=identity,
                defaults={"published": post_time or timezone.now()},
            )
        return post

    @staticmethod
    def get_post(post_pk: int) -> Post | None:
        return Post.objects.filter(pk=post_pk).first()

    @staticmethod
    def get_posts(post_pks: list[int]):
        return (
            Post.objects.filter(pk__in=post_pks)
            .exclude(state__in=["deleted", "deleted_fanned_out"])
            .prefetch_related("author", "attachments")
        )

    @staticmethod
    def get_post_url(post_pk: int) -> str | None:
        post = Post.objects.filter(pk=post_pk).first() if post_pk else None
        return post.object_uri if post else None

    @staticmethod
    def update_post(post_pk, **kwargs):
        Post.objects.filter(pk=post_pk).update(**kwargs)

    @staticmethod
    def delete_posts(post_pks):
        parent_posts = list(
            Post.objects.filter(
                object_uri__in=Post.objects.filter(
                    pk__in=post_pks, in_reply_to__isnull=False
                )
                .distinct("in_reply_to")
                .values_list("in_reply_to", flat=True)
            )
        )
        Post.objects.filter(pk__in=post_pks).update(state="deleted")
        for post in parent_posts:
            post.calculate_stats()
        # TimelineEvent.objects.filter(subject_post__in=[post.pk]).delete()
        PostInteraction.objects.filter(post__in=post_pks).update(state="undone")

    @staticmethod
    def visibility_n2t(visibility: int, post_public_mode: int) -> Visibilities:
        if visibility == 1:
            return Takahe.Visibilities.followers
        elif visibility == 2:
            return Takahe.Visibilities.mentioned
        elif post_public_mode == 4:
            return Takahe.Visibilities.local_only
        elif post_public_mode == 1:
            return Takahe.Visibilities.unlisted
        else:
            return Takahe.Visibilities.public

    @staticmethod
    def visibility_t2n(visibility: int) -> int:
        match visibility:
            case 2:
                return 1
            case 3:
                return 2
            case _:
                return 0

    @staticmethod
    def interact_post(post_pk: int, identity_pk: int, type: str, flip=False):
        post = Post.objects.filter(pk=post_pk).first()
        if not post:
            logger.warning(f"Cannot find post {post_pk}")
            return
        identity = Identity.objects.filter(pk=identity_pk).first()
        if not identity:
            logger.warning(f"Cannot find identity {identity_pk}")
            return
        interaction, created = PostInteraction.objects.get_or_create(
            type=type,
            identity_id=identity_pk,
            post=post,
        )
        if flip and not created and interaction.state != "undone_fanned_out":
            Takahe.update_state(interaction, "undone")
        elif interaction.state not in ["new", "fanned_out"]:
            Takahe.update_state(interaction, "new")
        post.calculate_stats()
        return interaction

    @staticmethod
    def uninteract_post(post_pk: int, identity_pk: int, type: str):
        post = Post.objects.filter(pk=post_pk).first()
        if not post:
            logger.warning(f"Cannot find post {post_pk}")
            return
        for interaction in PostInteraction.objects.filter(
            type=type,
            identity_id=identity_pk,
            post=post,
        ):
            interaction.state = "undone"
            interaction.save()
        post.calculate_stats()

    @staticmethod
    def reply_post(
        post_pk: int, identity_pk: int, content: str, visibility: Visibilities
    ):
        post = Post.objects.filter(pk=post_pk).first()
        if post and Takahe.get_is_blocking(post.author_id, identity_pk):
            return
        return Takahe.post(identity_pk, content, visibility, reply_to_pk=post_pk)

    @staticmethod
    def boost_post(post_pk: int, identity_pk: int):
        return Takahe.interact_post(post_pk, identity_pk, "boost", flip=True)

    @staticmethod
    def pin_post(post_pk: int, identity_pk: int):
        return Takahe.interact_post(post_pk, identity_pk, "pin", flip=True)

    @staticmethod
    def post_boosted_by(post_pk: int, identity_pk: int) -> bool:
        interaction = Takahe.get_user_interaction(post_pk, identity_pk, "boost")
        return interaction is not None and interaction.state in ["new", "fanned_out"]

    @staticmethod
    def like_post(post_pk: int, identity_pk: int):
        return Takahe.interact_post(post_pk, identity_pk, "like")

    @staticmethod
    def unlike_post(post_pk: int, identity_pk: int):
        return Takahe.uninteract_post(post_pk, identity_pk, "like")

    @staticmethod
    def post_liked_by(post_pk: int, identity_pk: int) -> bool:
        interaction = Takahe.get_user_interaction(post_pk, identity_pk, "like")
        return interaction is not None and interaction.state in ["new", "fanned_out"]

    @staticmethod
    def get_user_interaction(post_pk: int, identity_pk: int, type: str):
        if not post_pk or not identity_pk:
            return None
        post = Post.objects.filter(pk=post_pk).first()
        if not post:
            logger.warning(f"Cannot find post {post_pk}")
            return None
        return PostInteraction.objects.filter(
            type=type,
            identity_id=identity_pk,
            post=post,
        ).first()

    @staticmethod
    def get_post_stats(post_pk: int) -> dict:
        post = Post.objects.filter(pk=post_pk).first()
        if not post:
            logger.warning(f"Cannot find post {post_pk}")
            return {}
        return post.stats or {}

    @staticmethod
    def get_replies_for_posts(post_pks: list[int], identity_pk: int | None):
        post_uris = Post.objects.filter(pk__in=post_pks).values_list(
            "object_uri", flat=True
        )
        if not post_uris.exists():
            return Post.objects.none()
        identity = (
            Identity.objects.filter(pk=identity_pk).first() if identity_pk else None
        )
        child_queryset = (
            Post.objects.not_hidden()
            .prefetch_related(
                # "attachments",
                "mentions",
                "emojis",
            )
            .select_related(
                "author",
                "author__domain",
            )
            .filter(in_reply_to__in=post_uris)
            .order_by("published")
        )
        child_queryset = child_queryset.visible_to(
            identity=identity, include_replies=True
        )
        return child_queryset

    @staticmethod
    def html2txt(html: str) -> str:
        if not html:
            return ""
        return FediverseHtmlParser(html).plain_text

    @staticmethod
    def txt2html(txt: str) -> str:
        if not txt:
            return ""
        return FediverseHtmlParser(linebreaks_filter(txt)).html

    @staticmethod
    def update_state(
        obj: Post | PostInteraction | Relay | Identity | Domain | Follow, state: str
    ):
        obj.state = state
        obj.state_changed = timezone.now()
        obj.state_next_attempt = None
        obj.state_locked_until = None
        obj.save(
            update_fields=[
                "state",
                "state_changed",
                "state_next_attempt",
                "state_locked_until",
            ]
        )

    @staticmethod
    def get_neodb_peers():
        cache_key = "neodb_peers"
        peers = cache.get(cache_key, None)
        if peers is None:
            peers = list(
                Domain.objects.filter(
                    nodeinfo__protocols__contains="neodb",
                    nodeinfo__metadata__nodeEnvironment="production",
                    local=False,
                    blocked=False,
                ).values_list("pk", flat=True)
            )
            cache.set(cache_key, peers, timeout=1800)
        return peers

    @staticmethod
    def get_blocked_peers():
        cache_key = "blocked_peers"
        peers = cache.get(cache_key, None)
        if peers is None:
            peers = list(
                Domain.objects.filter(
                    local=False,
                    blocked=True,
                ).values_list("pk", flat=True)
            )
            cache.set(cache_key, peers, timeout=1800)
        return peers

    @staticmethod
    def verify_invite(token: str) -> bool:
        if not token:
            return False
        invite = Invite.objects.filter(token=token).first()
        return invite is not None and invite.valid

    @staticmethod
    def get_announcements():
        now = timezone.now()
        return Announcement.objects.filter(
            models.Q(start__lte=now) | models.Q(start__isnull=True),
            models.Q(end__gte=now) | models.Q(end__isnull=True),
            published=True,
        ).order_by("-start", "-created")

    @staticmethod
    def get_announcements_for_user(u: "NeoUser"):
        identity = (
            Identity.objects.filter(pk=u.identity.pk, local=True).first()
            if u and u.is_authenticated and u.identity
            else None
        )
        user = identity.users.all().first() if identity else None
        now = timezone.now()
        qs = Announcement.objects.filter(
            models.Q(start__lte=now) | models.Q(start__isnull=True),
            models.Q(end__gte=now) | models.Q(end__isnull=True),
            published=True,
        ).order_by("-start", "-created")
        return qs.exclude(seen=user) if user else qs

    @staticmethod
    def mark_announcements_seen(u: "NeoUser"):
        identity = (
            Identity.objects.filter(pk=u.identity.pk, local=True).first()
            if u and u.is_authenticated and u.identity
            else None
        )
        user = identity.users.all().first() if identity else None
        if not user:
            return
        now = timezone.now()
        for a in (
            Announcement.objects.filter(
                models.Q(start__lte=now) | models.Q(start__isnull=True),
                published=True,
            )
            .order_by("-start", "-created")
            .exclude(seen=user)
        ):
            a.seen.add(user)

    @staticmethod
    def get_events(identity_id: int, types: list[str], excl_self: bool = True):
        qs = (
            TimelineEvent.objects.select_related(
                "subject_post",
                "subject_post__author",
                "subject_post__author__domain",
                "subject_identity",
                "subject_identity__domain",
                "subject_post_interaction",
                "subject_post_interaction__identity",
                "subject_post_interaction__identity__domain",
            )
            .prefetch_related(
                "subject_post__attachments",
                "subject_post__mentions",
                "subject_post__emojis",
            )
            .filter(identity=identity_id)
            .filter(type__in=types)
            .order_by("-created")
        )
        if excl_self:
            qs = qs.exclude(subject_identity_id=identity_id)
        return qs

    @staticmethod
    def get_public_posts(local_only=False):
        qs = (
            Post.objects.exclude(state__in=["deleted", "deleted_fanned_out"])
            .filter(visibility__in=[0, 4])
            .filter(in_reply_to__isnull=True)
            .order_by("-published")
        )
        if local_only:
            qs = qs.filter(local=True)
        return qs.prefetch_related("attachments", "author")

    @staticmethod
    def get_recent_posts(author_pk: int, viewer_pk: int | None = None):
        since = timezone.now() - timedelta(days=90)
        qs = (
            Post.objects.exclude(state__in=["deleted", "deleted_fanned_out"])
            .filter(author_id=author_pk)
            .filter(published__gte=since)
            .order_by("-published")
        )
        if viewer_pk and Takahe.get_is_following(viewer_pk, author_pk):
            qs = qs.exclude(visibility=3)
        else:
            qs = qs.filter(visibility__in=[0, 1, 4])
        return qs.prefetch_related("attachments", "author")

    @staticmethod
    def pin_hashtag_for_user(identity_pk: int, hashtag: str):
        tag = Hashtag.ensure_hashtag(hashtag)
        identity = Identity.objects.get(pk=identity_pk)
        feature, created = identity.hashtag_features.get_or_create(hashtag=tag)
        if created:
            identity.fanout("tag_featured", subject_hashtag=tag)

    @staticmethod
    def unpin_hashtag_for_user(identity_pk: int, hashtag: str):
        identity = Identity.objects.get(pk=identity_pk)
        featured = HashtagFeature.objects.filter(
            identity=identity, hashtag_id=hashtag
        ).first()
        if featured:
            identity.fanout("tag_unfeatured", subject_hashtag_id=hashtag)
            featured.delete()

    @staticmethod
    def get_or_create_app(
        name: str,
        website: str,
        redirect_uris: str,
        owner_pk: int,
        scopes: str = "read write follow",
        client_id: str | None = None,
    ):
        client_id = client_id or (
            "app-" + b62_encode(owner_pk).zfill(11) + "-" + secrets.token_urlsafe(16)
        )
        client_secret = secrets.token_urlsafe(40)
        return Application.objects.get_or_create(
            client_id=client_id,
            defaults={
                "name": name,
                "website": website,
                "client_secret": client_secret,
                "redirect_uris": redirect_uris,
                "scopes": scopes,
            },
        )[0]

    @staticmethod
    def get_apps(owner_pk: int):
        return Application.objects.filter(
            name__startswith="app-" + b62_encode(owner_pk).zfill(11)
        )

    @staticmethod
    def refresh_token(app: Application, owner_pk: int, user_pk) -> str:
        tk = Token.objects.filter(application=app, identity_id=owner_pk).first()
        if tk:
            tk.delete()
        return Token.objects.create(
            application=app,
            identity_id=owner_pk,
            user_id=user_pk,
            scopes=["read", "write"],
            token=secrets.token_urlsafe(43),
        ).token

    @staticmethod
    def get_token(token: str) -> Token | None:
        return Token.objects.filter(token=token).first()

    @staticmethod
    def bookmark(post_pk: int, identity_pk: int):
        Bookmark.objects.get_or_create(post_id=post_pk, identity_id=identity_pk)

    @staticmethod
    def report_post(post: Post, reporter_pk: int, reason: str):
        src = Identity.objects.get(pk=reporter_pk)
        Report.objects.create(
            subject_identity=post.author,
            subject_post=post,
            source_identity=src,
            source_domain=src.domain,
            type="other",
            complaint=reason,
            forward=not post.local,
        )

    @staticmethod
    def get_poll_info(post, identity_pk: int):
        voted = post.interactions.filter(
            identity_id=identity_pk, type="vote", state__in=["new", "fanned_out"]
        ).values_list("value", flat=True)
        try:
            end_time = parse_datetime(post.type_data.get("end_time"))
        except ValueError:
            end_time = None
        info = post.type_data.copy()
        info["end_time"] = end_time
        info["ended"] = timezone.now() > end_time if end_time else True
        info["voted"] = post.author_id == identity_pk or voted.exists()
        for v in info["options"]:
            v["chosen"] = v["name"] in voted
        return info

    @staticmethod
    def vote_post(post, identity_pk, choices):
        try:
            end_time = parse_datetime(post.type_data.get("end_time"))
        except ValueError:
            end_time = None
        if end_time and timezone.now() > end_time:
            raise ValueError("Poll has ended")
        if post.author_id == identity_pk:
            raise ValueError("Cannot vote on your own poll")
        if post.interactions.filter(identity_id=identity_pk, type="vote").exists():
            raise ValueError("Already voted")
        options = [o["name"] for o in post.type_data["options"]]
        for choice in choices:
            if choice not in options:
                raise ValueError(f"Invalid choice: {choice}")
        identity = Identity.objects.get(pk=identity_pk)
        for choice in set(choices):
            vote = PostInteraction.objects.create(
                identity_id=identity_pk,
                post=post,
                type=PostInteraction.Types.vote,
                value=choice,
            )
            vote.object_uri = f"{identity.actor_uri}#votes/{vote.id}"
            vote.save()
        post.calculate_type_data()
