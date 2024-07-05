import functools
import typing
from datetime import timedelta
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.exceptions import RequestAborted
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from catalog.common import jsondata

from .common import SocialAccount

if typing.TYPE_CHECKING:
    from catalog.common.models import Item
    from journal.models.common import Content, VisibilityType

get = functools.partial(
    requests.get,
    timeout=settings.THREADS_TIMEOUT,
    headers={"User-Agent": settings.NEODB_USER_AGENT},
)
put = functools.partial(
    requests.put,
    timeout=settings.THREADS_TIMEOUT,
    headers={"User-Agent": settings.NEODB_USER_AGENT},
)
post = functools.partial(
    requests.post,
    timeout=settings.THREADS_TIMEOUT,
    headers={"User-Agent": settings.NEODB_USER_AGENT},
)
delete = functools.partial(
    requests.post,
    timeout=settings.THREADS_TIMEOUT,
    headers={"User-Agent": settings.NEODB_USER_AGENT},
)


class Threads:
    SCOPE = "threads_basic,threads_content_publish"
    DOMAIN = "threads.net"

    @staticmethod
    def generate_auth_url(request: HttpRequest):
        redirect_url = request.build_absolute_uri(reverse("mastodon:threads_oauth"))
        url = f"https://threads.net/oauth/authorize?client_id={settings.THREADS_APP_ID}&redirect_uri={redirect_url}&scope={Threads.SCOPE}&response_type=code"
        return url

    @staticmethod
    def obtain_token(
        request: HttpRequest, code: str
    ) -> tuple[str, int, str] | tuple[None, None, None]:
        redirect_url = request.build_absolute_uri(reverse("mastodon:threads_oauth"))
        payload = {
            "client_id": settings.THREADS_APP_ID,
            "client_secret": settings.THREADS_APP_SECRET,
            "redirect_uri": redirect_url,
            "grant_type": "authorization_code",
            "code": code,
        }
        url = "https://graph.threads.net/oauth/access_token"
        try:
            response = post(url, data=payload)
        except Exception as e:
            logger.warning(f"Error {url} {e}")
            return None, None, None
        if response.status_code != 200:
            logger.warning(f"Error {url} {response.status_code}")
            return None, None, None
        data = response.json()
        if data.get("error_type"):
            logger.warning(f"Error {url} {data}")
            return None, None, None
        short_token = data.get("access_token")
        user_id = data.get("user_id")

        # exchange for a 60-days token
        url = f"https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret={settings.THREADS_APP_SECRET}&access_token={short_token}"
        try:
            response = get(url)
        except Exception as e:
            logger.warning(f"Error {url} {e}")
            return None, None, None
        if response.status_code != 200:
            logger.warning(f"Error {url} {response.status_code}")
            return None, None, None
        data = response.json()
        if data.get("error_type"):
            logger.warning(f"Error {url} {data}")
            return None, None, None
        return data.get("access_token"), data.get("expires_in"), str(user_id)

    @staticmethod
    def refresh_token(token: str) -> tuple[str, int] | tuple[None, None]:
        url = f"https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token={token}"
        try:
            response = get(url)
        except Exception as e:
            logger.warning(f"Error {url} {e}")
            return None, None
        if response.status_code != 200:
            logger.warning(f"Error {url} {response.status_code}")
            return None, None
        data = response.json()
        if data.get("error_type"):
            logger.warning(f"Error {url} {data}")
            return None, None
        return data.get("access_token"), data.get("expires_in")

    @staticmethod
    def get_profile(
        token: str, user_id: str | None = None
    ) -> dict[str, str | int] | None:
        url = f'https://graph.threads.net/v1.0/{user_id or "me"}?fields=id,username,threads_profile_picture_url,threads_biography&access_token={token}'
        try:
            response = get(url)
        except Exception as e:
            logger.warning(f"Error {url} {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"Error {url} {response.status_code}")
            return None
        data = response.json()
        if data.get("error_type"):
            logger.warning(f"Error {url} {data}")
            return None
        return data

    @staticmethod
    def post_single(token: str, user_id: str, text: str, reply_to_id=None):
        url = f"https://graph.threads.net/v1.0/{user_id}/threads?media_type=TEXT&access_token={token}&text={quote(text)}"
        # TODO waiting for Meta to confirm it's bug or permission issue
        # if reply_to_id:
        #     url += "&reply_to_id=" + reply_to_id
        response = post(url)
        if response.status_code != 200:
            logger.debug(f"Error {url} {response.status_code} {response.content}")
            return None
        media_container_id = (response.json() or {}).get("id")
        if not media_container_id:
            return None
        url = f"https://graph.threads.net/v1.0/{user_id}/threads_publish?creation_id={media_container_id}&access_token={token}"
        response = post(url)
        if response.status_code != 200:
            logger.debug(f"Error {url} {response.status_code} {response.content}")
            return None
        media_id = (response.json() or {}).get("id")
        return media_id

    @staticmethod
    def get_single(token: str, media_id: str) -> dict | None:
        # url = f"https://graph.threads.net/v1.0/{media_id}?fields=id,media_product_type,media_type,media_url,permalink,owner,username,text,timestamp,shortcode,thumbnail_url,children,is_quote_post&access_token={token}"
        url = f"https://graph.threads.net/v1.0/{media_id}?fields=id,permalink,is_quote_post&access_token={token}"
        response = post(url)
        if response.status_code != 200:
            return None
        return response.json()

    @staticmethod
    def authenticate(request: HttpRequest, code: str) -> "ThreadsAccount | None":
        token, expire, uid = Threads.obtain_token(request, code)
        if not token or not expire:
            return None
        expires_at = timezone.now() + timedelta(seconds=expire)
        existing_account = ThreadsAccount.objects.filter(
            uid=uid, domain=Threads.DOMAIN
        ).first()
        if existing_account:
            existing_account.access_token = token
            existing_account.token_expires_at = expires_at
            existing_account.last_reachable = timezone.now()
            existing_account.save(update_fields=["access_data", "last_reachable"])
            existing_account.refresh()
            return existing_account
        account = ThreadsAccount()
        account.uid = uid
        account.access_token = token
        account.domain = Threads.DOMAIN
        account.token_expires_at = expires_at
        account.refresh(save=False)
        return account


class ThreadsAccount(SocialAccount):
    access_token = jsondata.EncryptedTextField(
        json_field_name="access_data", default=""
    )
    token_expires_at = jsondata.DateTimeField(json_field_name="access_data", null=True)
    username = jsondata.CharField(json_field_name="account_data", default="")
    threads_profile_picture_url = jsondata.CharField(
        json_field_name="account_data", default=""
    )
    threads_biography = jsondata.CharField(json_field_name="account_data", default="")

    @property
    def url(self):
        return f"https://threads.net/@{self.handle}"

    def check_alive(self, save=True) -> bool:
        # refresh token
        if not self.access_token:
            logger.warning(f"{self} token missing")
            return False
        if self.token_expires_at and timezone.now() > self.token_expires_at:
            logger.warning(f"{self} token expired")
            return False
        if self.last_reachable and timezone.now() < self.last_reachable + timedelta(
            hours=1
        ):
            return True
        token, expire = Threads.refresh_token(self.access_token)
        if not token or not expire:
            return False
        self.access_token = token
        self.last_reachable = timezone.now()
        self.token_expires_at = self.last_reachable + timedelta(seconds=expire)
        if save:
            self.save(update_fields=["access_data", "last_reachable"])
        return True

    def refresh(self, save=True) -> bool:
        if not self.access_token:
            logger.warning(f"{self} token missing")
            return False
        if self.token_expires_at and timezone.now() > self.token_expires_at:
            logger.warning(f"{self} token expired")
            return False
        data = Threads.get_profile(self.access_token)
        if not data:
            logger.warning(f"{self} unable to get profile")
            return False
        if self.handle != data["username"]:
            if self.handle:
                logger.info(f'{self} handle changed to {data["username"]}')
            self.handle = data["username"]
        self.account_data = data
        self.last_refresh = timezone.now()
        if save:
            self.save(update_fields=["account_data", "handle", "last_refresh"])
        return True

    def post(
        self,
        content: str,
        visibility: "VisibilityType",
        reply_to_id=None,
        obj: "Item | Content | None" = None,
        rating=None,
        **kwargs,
    ):
        from journal.models.renderers import render_rating

        text = (
            content.replace("##rating##", render_rating(rating))
            .replace("##obj_link_if_plain##", obj.absolute_url + "\n" if obj else "")
            .replace("##obj##", obj.display_title if obj else "")
        )
        media_id = Threads.post_single(self.access_token, self.uid, text, reply_to_id)
        if not media_id:
            raise RequestAborted()
        return {"id": media_id}
        # if media_id:
        #     d = Threads.get_single(self.access_token, media_id)
        #     if d:
        #         return {"id": media_id, "url": d["permalink"]}
