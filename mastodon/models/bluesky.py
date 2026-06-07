import base64
import hashlib
import json
import re
import typing
from functools import cached_property

from atproto import Client, SessionEvent, client_utils
from atproto_client import models
from atproto_client.exceptions import AtProtocolError
from atproto_identity.did.resolver import DidResolver
from atproto_identity.handle.resolver import HandleResolver
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from common.models import jsondata
from takahe.utils import Takahe

from .common import SocialAccount

if typing.TYPE_CHECKING:
    from catalog.models import Item
    from journal.models.common import Content


PROFILE_NSID = "net.neodb.profile"


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
            logger.warning(f"Bluesky login {handle} exception {e}")
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
            account.refresh(save=True, did_check=False)
        else:
            account.refresh(save=False, did_check=False)
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

    def get_reauthorize_url(self) -> str:
        return reverse("users:login") + "?method=atproto"

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

    def check_alive(self, save=True):
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
        self.last_reachable = timezone.now()
        if save:
            self.save(
                update_fields=[
                    "access_data",
                    "handle",
                    "last_reachable",
                ]
            )
        return True

    def refresh(self, save=True, did_check=True):
        if did_check:
            self.check_alive(save=save)
        try:
            profile = self._client.me
        except Exception as e:
            logger.warning(f"Bluesky: client error {self.handle} {e}")
            return False
        if not profile:
            logger.warning("Bluesky: client not logged in.")  # this should not happen
            return False
        if self.handle != profile.handle:
            if self.handle:
                logger.warning(
                    f"ATProto refresh: handle mismatch {self.handle} from did doc -> {profile.handle} from PDS"
                )
            self.handle = profile.handle
        self.account_data = {
            k: v for k, v in profile.__dict__.items() if isinstance(v, (int, str))
        }
        self.last_refresh = timezone.now()
        if save:
            self.save(
                update_fields=[
                    "access_data",
                    "account_data",
                    "last_refresh",
                    "handle",
                ]
            )
        self.sync_profile_record()
        return True

    @staticmethod
    def _jcs(data: dict) -> bytes:
        # JCS (RFC 8785) canonicalization; sorted compact JSON is equivalent
        # for objects whose values are all strings, as is the case here
        return json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()

    def _build_profile_record(self) -> dict[str, typing.Any]:
        from journal.models.atproto import format_datetime

        identity = self.user.identity
        takahe_identity = identity.takahe_identity
        record: dict[str, typing.Any] = {
            "$type": PROFILE_NSID,
            "did": self.uid,
            "actor": identity.actor_uri,
            "url": settings.SITE_INFO["site_url"] + identity.url,
            "handle": identity.full_handle,
            "createdAt": format_datetime(self.created),
        }
        if takahe_identity.private_key and takahe_identity.public_key_id:
            # sign the statement with the actor's federation key so the link
            # verifies in both directions: the record living in the DID's
            # repo proves the DID side, the signature proves the actor side.
            # modeled on FEP-c390 / W3C Data Integrity (eddsa-jcs-2022
            # procedure with an RSA suite, as AP federation keys are RSA)
            key = serialization.load_pem_private_key(
                takahe_identity.private_key.encode(), password=None
            )
            if isinstance(key, rsa.RSAPrivateKey):
                proof: dict[str, typing.Any] = {
                    "type": "DataIntegrityProof",
                    "cryptosuite": "rsa-pkcs1-sha256-jcs",
                    "created": record["createdAt"],
                    "verificationMethod": takahe_identity.public_key_id,
                    "proofPurpose": "assertionMethod",
                }
                data = (
                    hashlib.sha256(self._jcs(proof)).digest()
                    + hashlib.sha256(self._jcs(record)).digest()
                )
                signature = key.sign(data, padding.PKCS1v15(), hashes.SHA256())
                proof["proofValue"] = base64.b64encode(signature).decode()
                record["proof"] = proof
        return record

    def sync_profile_record(self) -> None:
        """Reconcile the net.neodb.profile record linking this DID to the
        owner's NeoDB identity.

        Written only while the identity is publicly discoverable -- PDS
        records are world-readable -- and deleted otherwise (both idempotent).
        """
        identity = self.user.identity if self.user else None
        if not identity:
            return
        try:
            if identity.discoverable:
                self.put_record(PROFILE_NSID, "self", self._build_profile_record())
            else:
                self.delete_record(PROFILE_NSID, "self")
        except Exception as e:
            logger.warning(f"{self} profile record sync error {e}")

    def on_disconnect(self) -> None:
        try:
            self.delete_record(PROFILE_NSID, "self")
        except Exception as e:
            logger.warning(f"{self} profile record cleanup error {e}")

    def _paginate_dids(
        self,
        fetch: "typing.Callable[[str | None], typing.Any]",
        attr: str,
        max_pages: int = 200,
    ) -> list[str]:
        """Accumulate DIDs across every page of a cursored graph listing.

        ``fetch(cursor)`` returns a response exposing ``.cursor`` and a list
        attribute named ``attr``. ``max_pages`` (100 entries per page) bounds
        the loop against a non-terminating cursor; truncation is logged rather
        than failing silently.
        """
        dids: list[str] = []
        cursor: str | None = None
        for _ in range(max_pages):
            r = fetch(cursor)
            dids += [p.did for p in getattr(r, attr)]
            cursor = r.cursor
            if not cursor:
                break
        else:
            logger.warning(
                f"{self} graph listing '{attr}' truncated at {len(dids)} entries"
            )
        return dids

    def refresh_graph(self, save=True) -> bool:
        try:
            self.following = self._paginate_dids(
                lambda c: self._client.get_follows(self.uid, cursor=c, limit=100),
                "follows",
            )
            self.followers = self._paginate_dids(
                lambda c: self._client.get_followers(self.uid, cursor=c, limit=100),
                "followers",
            )
            self.mutes = self._paginate_dids(
                lambda c: self._client.app.bsky.graph.get_mutes(
                    models.AppBskyGraphGetMutes.Params(cursor=c, limit=100)
                ),
                "mutes",
            )
        except AtProtocolError as e:
            logger.warning(f"{self} refresh_graph error: {e}")
            return False
        if save:
            self.save(
                update_fields=[
                    "followers",
                    "following",
                    "mutes",
                ]
            )
        return True

    def sync_graph(self):
        c = 0

        def get_identity_ids(accts: list):
            return set(
                BlueskyAccount.objects.filter(
                    domain=Bluesky._DOMAIN, uid__in=accts
                ).values_list("user__identity", flat=True)
            )

        me = self.user.identity.pk
        for target_identity in get_identity_ids(self.following):
            if not Takahe.get_is_following(me, target_identity):
                Takahe.follow(me, target_identity, True)
                c += 1

        for target_identity in get_identity_ids(self.mutes):
            if not Takahe.get_is_muting(me, target_identity):
                Takahe.mute(me, target_identity)
                c += 1

        return c

    def post(
        self,
        content,
        reply_to_id=None,
        obj: "Item | Content | EmbedObj | None" = None,
        rating=None,
        images=[],
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
        txt = content.replace("##rating##", render_rating(rating)).replace(
            "##obj_link_if_plain##", ""
        )
        max_len = 280  # maxGraphemes of app.bsky.feed.post is 300, but just be safe
        if len(txt) + len(obj.display_title if obj else "") > max_len:
            txt = txt[: max_len - len(obj.display_title if obj else "")] + "……"
        text = txt.split("##obj##")
        richtext = client_utils.TextBuilder()
        first = True
        for t in text:
            if not first and obj:
                richtext.link(obj.display_title, obj.absolute_url)
            else:
                first = False
            richtext.text(t)
        if images:
            refs = [self._client.upload_blob(image).blob for image in images]
            embed_images = [
                models.AppBskyEmbedImages.Image(alt="", image=r) for r in refs
            ]
            embed = models.AppBskyEmbedImages.Main(images=embed_images)
        elif obj:
            cover = getattr(obj, "cover", None)
            blob = (
                cover.read()
                if cover and cover != settings.DEFAULT_ITEM_COVER
                else getattr(obj, "image", None)
            )
            max_size = 1000000  # maxSize of app.bsky.embed.external
            if blob and len(blob) <= max_size:
                blob_ref = self._client.upload_blob(blob).blob
            else:
                blob_ref = None
            embed = models.AppBskyEmbedExternal.Main(
                external=models.AppBskyEmbedExternal.External(
                    title=obj.display_title,
                    description=obj.brief_description,
                    uri=obj.absolute_url,
                    thumb=blob_ref,
                )
            )
        else:
            embed = None
        post = self._client.send_post(richtext, reply_to=reply_to, embed=embed)
        # return AT uri as id since it's used as so.
        return {"cid": post.cid, "id": post.uri}

    def delete_post(self, post_uri):
        self._client.delete_post(post_uri)

    def put_record(
        self, collection: str, rkey: str, record: dict[str, typing.Any]
    ) -> dict[str, str]:
        """Create or overwrite a record in the user's repo.

        Idempotent: the same ``rkey`` updates the existing record in place,
        so editing a NeoDB piece overwrites its record rather than duplicating
        it. ``record`` must contain a ``$type`` field.
        """
        r = self._client.com.atproto.repo.put_record(
            models.ComAtprotoRepoPutRecord.Data(
                repo=self.uid,
                collection=collection,
                rkey=rkey,
                record=record,
            )
        )
        return {"uri": r.uri, "cid": r.cid}

    def delete_record(self, collection: str, rkey: str) -> None:
        """Delete a record by key. Idempotent: no error if it does not exist."""
        self._client.com.atproto.repo.delete_record(
            models.ComAtprotoRepoDeleteRecord.Data(
                repo=self.uid, collection=collection, rkey=rkey
            )
        )


class EmbedObj:
    def __init__(self, title, description, uri, image=None):
        self.display_title = title
        self.brief_description = description
        self.absolute_url = uri
        self.image = image
