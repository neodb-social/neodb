import re
import typing
from functools import cached_property

from atproto import Client, SessionEvent, client_utils
from atproto_client import models
from atproto_identity.did.resolver import DidResolver
from atproto_identity.handle.resolver import HandleResolver
from django.utils import timezone
from loguru import logger

from catalog.common import jsondata

from .common import SocialAccount

if typing.TYPE_CHECKING:
    from catalog.common.models import Item
    from journal.models.common import Content


class Bluesky:
    _DOMAIN = "-"
    _RE_HANDLE = re.compile(
        r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
    )
    # for BlueskyAccount
    # uid is did and the only unique identifier
    # domain is not useful and will always be _DOMAIN
    # handle and base_url may change in BlueskyAccount.refresh()

    @staticmethod
    def authenticate(handle: str, password: str) -> "BlueskyAccount | None":
        if not Bluesky._RE_HANDLE.match(handle) or len(handle) > 500:
            logger.warning(f"ATProto login failed: handle {handle} is invalid")
            return None
        try:
            handle_r = HandleResolver(timeout=5)
            did = handle_r.resolve(handle)
            if not did:
                logger.warning(
                    f"ATProto login failed: handle {handle} -> <missing did>"
                )
                return
            did_r = DidResolver()
            did_doc = did_r.resolve(did)
            if not did_doc:
                logger.warning(
                    f"ATProto login failed: handle {handle} -> did {did} -> <missing doc>"
                )
                return
            resolved_handle = did_doc.get_handle()
            if resolved_handle != handle:
                logger.warning(
                    f"ATProto login failed: handle {handle} -> did {did} -> handle {resolved_handle}"
                )
                return
            base_url = did_doc.get_pds_endpoint()
            client = Client(base_url)
            profile = client.login(handle, password)
            session_string = client.export_session_string()
        except Exception as e:
            logger.debug(f"Bluesky login {handle} exception {e}")
            return
        account = BlueskyAccount.objects.filter(
            uid=profile.did, domain=Bluesky._DOMAIN
        ).first()
        if not account:
            account = BlueskyAccount(uid=profile.did, domain=Bluesky._DOMAIN)
        account._client = client
        account.session_string = session_string
        account.base_url = base_url
        if account.pk:
            account.refresh(save=True, did_refresh=False)
        else:
            account.refresh(save=False, did_refresh=False)
        return account


class BlueskyAccount(SocialAccount):
    # app_username = jsondata.CharField(json_field_name="access_data", default="")
    # app_password = jsondata.EncryptedTextField(
    #     json_field_name="access_data", default=""
    # )
    base_url = jsondata.CharField(json_field_name="access_data", default=None)
    session_string = jsondata.EncryptedTextField(
        json_field_name="access_data", default=""
    )
    display_name = jsondata.CharField(json_field_name="account_data", default="")
    description = jsondata.CharField(json_field_name="account_data", default="")
    avatar = jsondata.CharField(json_field_name="account_data", default="")

    def on_session_change(self, event, session) -> None:
        if event in (SessionEvent.CREATE, SessionEvent.REFRESH):
            session_string = session.export()
            if session_string != self.session_string:
                self.session_string = session_string
                if self.pk:
                    self.save(update_fields=["access_data"])

    @cached_property
    def _client(self):
        client = Client()
        client.on_session_change(self.on_session_change)
        self._profile = client.login(session_string=self.session_string)
        return client

    @property
    def url(self):
        return f"https://{self.handle}"

    def refresh(self, save=True, did_refresh=True):
        if did_refresh:
            did = self.uid
            did_r = DidResolver()
            handle_r = HandleResolver(timeout=5)
            did_doc = did_r.resolve(did)
            if not did_doc:
                logger.warning(f"ATProto refresh failed: did {did} -> <missing doc>")
                return False
            resolved_handle = did_doc.get_handle()
            if not resolved_handle:
                logger.warning(f"ATProto refresh failed: did {did} -> <missing handle>")
                return False
            resolved_did = handle_r.resolve(resolved_handle)
            resolved_pds = did_doc.get_pds_endpoint()
            if did != resolved_did:
                logger.warning(
                    f"ATProto refresh failed: did {did} -> handle {resolved_handle} -> did {resolved_did}"
                )
                return False
            if resolved_handle != self.handle:
                logger.debug(
                    f"ATProto refresh: handle changed for did {did}: handle {self.handle} -> {resolved_handle}"
                )
                self.handle = resolved_handle
            if resolved_pds != self.base_url:
                logger.debug(
                    f"ATProto refresh: pds changed for did {did}: handle {self.base_url} -> {resolved_pds}"
                )
                self.base_url = resolved_pds
        profile = self._client.me
        if not profile:
            logger.warning("Bluesky: client not logged in.")  # this should not happen
            return None
        if self.handle != profile.handle:
            logger.warning(
                "ATProto refresh: handle mismatch {self.handle} from did doc -> {profile.handle} from PDS"
            )
        self.account_data = {
            k: v for k, v in profile.__dict__.items() if isinstance(v, (int, str))
        }
        self.last_refresh = timezone.now()
        self.last_reachable = self.last_refresh
        if save:
            self.save(
                update_fields=[
                    "access_data",
                    "account_data",
                    "handle",
                    "last_refresh",
                    "last_reachable",
                ]
            )

    def post(
        self,
        content,
        reply_to_id=None,
        obj: "Item | Content | None" = None,
        rating=None,
        **kwargs,
    ):
        from journal.models.renderers import render_rating

        reply_to = None
        if reply_to_id:
            posts = self._client.get_posts([reply_to_id]).posts
            if posts:
                root_post_ref = models.create_strong_ref(posts[0])
                reply_to = models.AppBskyFeedPost.ReplyRef(
                    parent=root_post_ref, root=root_post_ref
                )
        text = (
            content.replace("##rating##", render_rating(rating))
            .replace("##obj_link_if_plain##", "")
            .split("##obj##")
        )
        richtext = client_utils.TextBuilder()
        first = True
        for t in text:
            if not first and obj:
                richtext.link(obj.display_title, obj.absolute_url)
            else:
                first = False
            richtext.text(t)
        if obj:
            embed = models.AppBskyEmbedExternal.Main(
                external=models.AppBskyEmbedExternal.External(
                    title=obj.display_title,
                    description=obj.display_description,
                    uri=obj.absolute_url,
                )
            )
        else:
            embed = None
        post = self._client.send_post(richtext, reply_to=reply_to, embed=embed)
        # return AT uri as id since it's used as so.
        return {"cid": post.cid, "id": post.uri}

    def delete_post(self, post_uri):
        self._client.delete_post(post_uri)
