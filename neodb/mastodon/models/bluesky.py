import base64
import hashlib
import json
import re
import secrets
import time
import typing
from functools import cached_property
from urllib.parse import quote, urlencode

from atproto import Client, SessionEvent, client_utils
from atproto_client import models
from atproto_client.exceptions import AtProtocolError
from atproto_identity.did.resolver import DidResolver
from atproto_identity.handle.resolver import HandleResolver
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from common.models import jsondata
from takahe.utils import Takahe

from .bluesky_oauth import (
    DpopRequest,
    OAuthError,
    fetch_authserver_metadata,
    fetch_pds_authserver,
    generate_dpop_jwk,
    get_client_id,
    initial_token_request,
    refresh_token_request,
    send_par,
)
from .common import SocialAccount

if typing.TYPE_CHECKING:
    from catalog.models import Item
    from journal.models.common import Content


PROFILE_NSID = "net.neodb.profile"

_OAUTH_SESSION_KEY = "atproto_oauth"
# refresh the access token when it expires within this margin
_TOKEN_EXPIRY_MARGIN = 60


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
    def _resolve_identity(handle: str) -> tuple[str, str]:
        """handle -> (did, pds endpoint), with bidirectional verification."""
        handle_r = HandleResolver(timeout=5)
        did = handle_r.resolve(handle)
        if not did:
            raise OAuthError(f"handle {handle} not found")
        did_r = DidResolver()
        did_doc = did_r.resolve(did)
        if not did_doc:
            raise OAuthError(f"DID document for {did} not found")
        resolved_handle = did_doc.get_handle()
        if resolved_handle != handle:
            raise OAuthError(f"handle {handle} does not match its DID document")
        pds_url = did_doc.get_pds_endpoint()
        if not pds_url:
            raise OAuthError(f"no PDS endpoint for {did}")
        return did, pds_url

    @staticmethod
    def generate_auth_url(handle: str, request: HttpRequest) -> str:
        """Resolve the handle, push an authorization request to its auth
        server and return the URL to redirect the user to; the pending
        authorization state is kept in the django session."""
        handle = handle.strip().lstrip("@").lower()
        if not Bluesky._RE_HANDLE.match(handle) or len(handle) > 500:
            raise OAuthError("invalid handle")
        did, pds_url = Bluesky._resolve_identity(handle)
        issuer = fetch_pds_authserver(pds_url)
        meta = fetch_authserver_metadata(issuer)
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        dpop_jwk = generate_dpop_jwk()
        request_uri, nonce = send_par(
            meta, dpop_jwk, handle=handle, state=state, code_verifier=code_verifier
        )
        request.session[_OAUTH_SESSION_KEY] = {
            "state": state,
            "did": did,
            "handle": handle,
            "pds_url": pds_url,
            "issuer": meta["issuer"],
            "token_endpoint": meta["token_endpoint"],
            "code_verifier": code_verifier,
            "dpop_jwk": dpop_jwk,
            "authserver_nonce": nonce,
        }
        query = urlencode({"client_id": get_client_id(), "request_uri": request_uri})
        return f"{meta['authorization_endpoint']}?{query}"

    @staticmethod
    def receive_oauth_code(
        request: HttpRequest, code: str, state: str, iss: str | None
    ) -> "BlueskyAccount | None":
        """Complete the authorization code flow and return the account."""
        pending = request.session.pop(_OAUTH_SESSION_KEY, None)
        if not pending or not secrets.compare_digest(
            pending["state"].encode(), state.encode()
        ):
            logger.warning("ATProto OAuth failed: state mismatch")
            return None
        if iss and iss.rstrip("/") != pending["issuer"].rstrip("/"):
            logger.warning(f"ATProto OAuth failed: iss mismatch {iss}")
            return None
        try:
            tokens, nonce = initial_token_request(
                pending["token_endpoint"],
                pending["issuer"],
                code,
                pending["code_verifier"],
                pending["dpop_jwk"],
                pending["authserver_nonce"],
            )
        except OAuthError as e:
            logger.warning(f"ATProto OAuth token exchange failed: {e}")
            return None
        did = tokens.get("sub")
        if did != pending["did"]:
            logger.warning(f"ATProto OAuth failed: sub {did} != {pending['did']}")
            return None
        if "atproto" not in (tokens.get("scope") or "").split():
            logger.warning("ATProto OAuth failed: atproto scope not granted")
            return None
        account = BlueskyAccount.objects.filter(uid=did, domain=Bluesky._DOMAIN).first()
        if not account:
            account = BlueskyAccount(uid=did, domain=Bluesky._DOMAIN)
        account.handle = pending["handle"]
        account.base_url = pending["pds_url"]
        account.session_string = ""  # drop legacy app-password session if any
        account._set_oauth(
            {
                "issuer": pending["issuer"],
                "token_endpoint": pending["token_endpoint"],
                "dpop_jwk": pending["dpop_jwk"],
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token", ""),
                "expires_at": int(time.time()) + int(tokens.get("expires_in") or 300),
                "scope": tokens.get("scope", ""),
                "authserver_nonce": nonce,
                "pds_nonce": "",
            }
        )
        if account.pk:
            account.refresh(save=True, did_check=False)
        else:
            account.refresh(save=False, did_check=False)
        return account


class BlueskyAccount(SocialAccount):
    base_url = jsondata.CharField(json_field_name="access_data", default=None)
    # legacy app-password session; still used by accounts that have not
    # re-authorized via OAuth yet
    session_string = jsondata.EncryptedTextField(
        json_field_name="access_data", default=""
    )
    # JSON-encoded OAuth session: tokens, DPoP key and server nonces
    oauth_session = jsondata.EncryptedTextField(
        json_field_name="access_data", default=""
    )
    display_name = jsondata.CharField(json_field_name="account_data", default="")
    description = jsondata.CharField(json_field_name="account_data", default="")
    avatar = jsondata.CharField(json_field_name="account_data", default="")

    _oauth_cache: dict | None = None

    def get_reauthorize_url(self) -> str:
        url = reverse("users:login") + "?method=bluesky"
        if self.handle:
            url += "&username=" + quote(self.handle)
        return url

    def _get_oauth(self) -> dict:
        if self._oauth_cache is None:
            try:
                self._oauth_cache = (
                    json.loads(self.oauth_session) if self.oauth_session else {}
                )
            except json.JSONDecodeError:
                self._oauth_cache = {}
        return self._oauth_cache

    def _set_oauth(self, session: dict, save: bool = False) -> None:
        self._oauth_cache = session
        self.oauth_session = json.dumps(session)
        if save and self.pk:
            self.save(update_fields=["access_data"])

    def get_dpop_jwk(self) -> dict:
        return self._get_oauth().get("dpop_jwk") or {}

    def get_pds_nonce(self) -> str:
        return self._get_oauth().get("pds_nonce") or ""

    def save_pds_nonce(self, nonce: str) -> None:
        session = self._get_oauth()
        session["pds_nonce"] = nonce
        self._set_oauth(session, save=True)

    def get_access_token(self, force_refresh: bool = False) -> str:
        session = self._get_oauth()
        if not session.get("access_token"):
            raise OAuthError("no OAuth session for this account")
        if (
            force_refresh
            or int(session.get("expires_at") or 0) < time.time() + _TOKEN_EXPIRY_MARGIN
        ):
            self._refresh_oauth_token()
            session = self._get_oauth()
        return session["access_token"]

    def _refresh_oauth_token(self) -> None:
        # refresh tokens are single-use, so serialize refreshes across
        # workers and pick up tokens another worker may have just rotated
        lock_key = f"bluesky_oauth_refresh_{self.uid}"
        acquired = cache.add(lock_key, 1, timeout=60)
        try:
            if not acquired:
                for _ in range(20):
                    time.sleep(0.5)
                    if not cache.get(lock_key):
                        break
            if self.pk:
                fresh = BlueskyAccount.objects.filter(pk=self.pk).first()
                if fresh and fresh.access_data != self.access_data:
                    self.access_data = fresh.access_data
                    self._oauth_cache = None
            session = self._get_oauth()
            expires_at = int(session.get("expires_at") or 0)
            if expires_at > time.time() + _TOKEN_EXPIRY_MARGIN:
                return  # already refreshed by another worker
            if not session.get("refresh_token"):
                raise OAuthError("OAuth session expired, re-authorization needed")
            tokens, nonce = refresh_token_request(
                session["token_endpoint"],
                session["issuer"],
                session["refresh_token"],
                session["dpop_jwk"],
                session.get("authserver_nonce", ""),
            )
            session.update(
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token", session["refresh_token"]),
                expires_at=int(time.time()) + int(tokens.get("expires_in") or 300),
                authserver_nonce=nonce,
            )
            self._set_oauth(session, save=True)
        finally:
            if acquired:
                cache.delete(lock_key)

    def on_session_change(self, event, session) -> None:
        if event in (SessionEvent.CREATE, SessionEvent.REFRESH):
            session_string = session.export()
            if session_string != self.session_string:
                self.session_string = session_string
                if self.pk:
                    self.save(update_fields=["access_data"])

    @cached_property
    def _client(self):
        if self._get_oauth().get("access_token"):
            client = Client(self.base_url, request=DpopRequest(self))
            self._profile = client.app.bsky.actor.get_profile(
                models.AppBskyActorGetProfile.Params(actor=self.uid)
            )
            client.me = self._profile
            return client
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
            ) - {None}

        me = self.user.identity.pk

        follow_targets = get_identity_ids(self.following)
        already_following = Takahe.get_existing_following_ids(me, follow_targets)
        for target_identity in follow_targets - already_following:
            Takahe.follow(me, target_identity, True)
            c += 1

        mute_targets = get_identity_ids(self.mutes)
        already_muting = Takahe.get_existing_muting_ids(me, mute_targets)
        for target_identity in mute_targets - already_muting:
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
        fediverse_uri: str | None = None,
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
        # Build the record by hand (rather than client.send_post) so we can
        # attach an off-lexicon neodbOriginalUrl pointing at the originating
        # fediverse post
        langs = (
            [self.user.macrolanguage] if self.user and self.user.macrolanguage else None
        )
        record = models.AppBskyFeedPost.Record(
            created_at=self._client.get_current_time_iso(),
            text=richtext.build_text(),
            facets=richtext.build_facets(),
            reply=reply_to,
            embed=embed,
            langs=langs,
        )
        if fediverse_uri:
            setattr(record, "neodbOriginalUrl", fediverse_uri)
        post = self._client.app.bsky.feed.post.create(self.uid, record)
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
