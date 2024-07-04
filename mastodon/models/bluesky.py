from functools import cached_property
from operator import pos

from atproto import Client, SessionEvent, client_utils
from atproto_client import models
from django.utils import timezone
from loguru import logger

from catalog.common import jsondata

from .common import SocialAccount


class Bluesky:
    BASE_DOMAIN = "bsky.app"  # TODO support alternative servers

    @staticmethod
    def authenticate(username: str, password: str) -> "BlueskyAccount | None":
        try:
            client = Client()
            profile = client.login(username, password)
            session_string = client.export_session_string()
        except Exception as e:
            logger.debug(f"Bluesky login {username} exception {e}")
            return None
        existing_account = BlueskyAccount.objects.filter(
            uid=profile.did, domain=Bluesky.BASE_DOMAIN
        ).first()
        if existing_account:
            existing_account.session_string = session_string
            existing_account.save(update_fields=["access_data"])
            existing_account.refresh(save=True, profile=profile)
            return existing_account
        account = BlueskyAccount(uid=profile.did, domain=Bluesky.BASE_DOMAIN)
        account.session_string = session_string
        account.refresh(save=False, profile=profile)
        return account


class BlueskyAccount(SocialAccount):
    # app_username = jsondata.CharField(json_field_name="access_data", default="")
    # app_password = jsondata.EncryptedTextField(
    #     json_field_name="access_data", default=""
    # )
    session_string = jsondata.EncryptedTextField(
        json_field_name="access_data", default=""
    )

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
        return f"https://bsky.app/profile/{self.handle}"

    def refresh(self, save=True, profile=None):
        if not profile:
            _ = self._client
            profile = self._profile
        self.handle = profile.handle
        self.account_data = {
            k: v for k, v in profile.__dict__.items() if isinstance(v, (int, str))
        }
        self.last_refresh = timezone.now()
        self.last_reachable = self.last_refresh
        if save:
            self.save(
                update_fields=[
                    "account_data",
                    "handle",
                    "last_refresh",
                    "last_reachable",
                ]
            )

    def post(self, content, reply_to_id=None, **kwargs):
        reply_to = None
        if reply_to_id:
            posts = self._client.get_posts([reply_to_id]).posts
            if posts:
                root_post_ref = models.create_strong_ref(posts[0])
                reply_to = models.AppBskyFeedPost.ReplyRef(
                    parent=root_post_ref, root=root_post_ref
                )
        text = client_utils.TextBuilder().text(content)
        # todo OpenGraph
        # .link("Python SDK", "https://atproto.blue")
        post = self._client.send_post(text, reply_to=reply_to)
        # return AT uri as id since it's used as so.
        return {"cid": post.cid, "id": post.uri}

    def delete_post(self, post_uri):
        self._client.delete_post(post_uri)
