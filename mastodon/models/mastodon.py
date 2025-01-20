import functools
import random
import re
import string
import typing
from enum import StrEnum
from urllib.parse import quote

import django_rq
import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, RequestAborted
from django.db import models
from django.db.models import Count
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

# from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _
from loguru import logger

from catalog.common import jsondata
from takahe.utils import Takahe

from .common import SocialAccount

if typing.TYPE_CHECKING:
    from catalog.common.models import Item
    from journal.models.common import Content, VisibilityType


class TootVisibilityEnum(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    DIRECT = "direct"
    UNLISTED = "unlisted"


get = functools.partial(requests.get, timeout=settings.MASTODON_TIMEOUT)
put = functools.partial(requests.put, timeout=settings.MASTODON_TIMEOUT)
post = functools.partial(requests.post, timeout=settings.MASTODON_TIMEOUT)
delete = functools.partial(requests.post, timeout=settings.MASTODON_TIMEOUT)
_sites_cache_key = "login_sites"

# See https://docs.joinmastodon.org/methods/accounts/

# returns user info
# retruns the same info as verify account credentials
# GET
API_GET_ACCOUNT = "/api/v1/accounts/:id"

# returns user info if valid, 401 if invalid
# GET
API_VERIFY_ACCOUNT = "/api/v1/accounts/verify_credentials"

# obtain token
# GET
API_OBTAIN_TOKEN = "/oauth/token"

# obatin auth code
# GET
API_OAUTH_AUTHORIZE = "/oauth/authorize"

# revoke token
# POST
API_REVOKE_TOKEN = "/oauth/revoke"

# relationships
# GET
API_GET_RELATIONSHIPS = "/api/v1/accounts/relationships"

# toot
# POST
API_PUBLISH_TOOT = "/api/v1/statuses"

# create new app
# POST
API_CREATE_APP = "/api/v1/apps"

# search
# GET
API_SEARCH = "/api/v2/search"

USER_AGENT = settings.NEODB_USER_AGENT


def get_api_domain(domain):
    app = MastodonApplication.objects.filter(domain_name=domain).first()
    return app.api_domain if app and app.api_domain else domain


# low level api below


def boost_toot(domain, token, toot_url):
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    url = (
        "https://"
        + domain
        + API_SEARCH
        + "?type=statuses&resolve=true&q="
        + quote(toot_url)
    )
    try:
        response = get(url, headers=headers)
        if response.status_code != 200:
            logger.warning(
                f"Error search {toot_url} on {domain} {response.status_code}"
            )
            return None
        j = response.json()
        if "statuses" in j and len(j["statuses"]) > 0:
            s = j["statuses"][0]
            url_id = toot_url.split("/posts/")[-1]
            url_id2 = s["uri"].split("/posts/")[-1]
            if s["uri"] != toot_url and s["url"] != toot_url and url_id != url_id2:
                logger.warning(
                    f"Error status url mismatch {s['uri']} or {s['uri']} != {toot_url}"
                )
                return None
            if s["reblogged"]:
                logger.warning(f"Already boosted {toot_url}")
                # TODO unboost and boost again?
                return None
            url = (
                "https://"
                + domain
                + API_PUBLISH_TOOT
                + "/"
                + j["statuses"][0]["id"]
                + "/reblog"
            )
            response = post(url, headers=headers)
            if response.status_code != 200:
                logger.warning(
                    f"Error search {toot_url} on {domain} {response.status_code}"
                )
                return None
            return response.json()
    except Exception:
        logger.warning(f"Error search {toot_url} on {domain}")
        return None


def delete_toot(api_domain, access_token, toot_id):
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {access_token}",
    }
    url = "https://" + api_domain + API_PUBLISH_TOOT + "/" + toot_id
    try:
        response = delete(url, headers=headers)
        if response.status_code != 200:
            logger.warning(f"Error DELETE {url} {response.status_code}")
    except Exception as e:
        logger.warning(f"Error deleting {e}")


def post_toot2(
    api_domain: str,
    access_token: str,
    content: str,
    visibility: TootVisibilityEnum,
    update_id: str | None = None,
    reply_to_id: str | None = None,
    sensitive: bool = False,
    spoiler_text: str | None = None,
    attachments: list = [],
):
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": random_string_generator(16),
    }
    base_url = "https://" + api_domain
    response = None
    url = base_url + API_PUBLISH_TOOT
    payload = {
        "status": content,
        "visibility": visibility,
    }
    if reply_to_id:
        payload["in_reply_to_id"] = reply_to_id
    if spoiler_text:
        payload["spoiler_text"] = spoiler_text
    if sensitive:
        payload["sensitive"] = True
    media_ids = []
    for atta in attachments:
        try:
            media_id = (
                post(
                    base_url + "/api/v1/media",
                    headers=headers,
                    data={},
                    files={"file": atta},
                )
                .json()
                .get("id")
            )
            media_ids.append(media_id)
        except Exception as e:
            logger.warning(f"Error uploading image {e}")
        headers["Idempotency-Key"] = random_string_generator(16)
    if media_ids:
        payload["media_ids[]"] = media_ids
    try:
        if update_id:
            response = put(url + "/" + update_id, headers=headers, data=payload)
        if not update_id or (response is not None and response.status_code != 200):
            headers["Idempotency-Key"] = random_string_generator(16)
            response = post(url, headers=headers, data=payload)
        if response is not None and response.status_code != 200:
            headers["Idempotency-Key"] = random_string_generator(16)
            payload["in_reply_to_id"] = None
            response = post(url, headers=headers, data=payload)
        if response is not None and response.status_code == 201:
            response.status_code = 200
        if response is not None and response.status_code != 200:
            logger.warning(f"Error {url} {response.status_code}")
    except Exception as e:
        logger.warning(f"Error posting {e}")
        response = None
    return response


def _get_redirect_uris(server_version: str) -> str:
    allow_multiple_redir = not (
        re.match(r".*(Pixelfed|Friendica).*", server_version or "")
        or re.match(r"^0\..*", server_version or "")
    )  # GoToSocial and a few don't support multiple redirect uris
    u = settings.SITE_INFO["site_url"] + "/account/login/oauth"
    if not allow_multiple_redir:
        return u
    u2s = [f"https://{d}/account/login/oauth" for d in settings.ALTERNATIVE_DOMAINS]
    return "\n".join([u] + u2s)


def _get_scopes(server_version: str) -> str:
    return (
        settings.MASTODON_LEGACY_CLIENT_SCOPE
        if re.match(r".*(Pixelfed|Friendica).*", server_version or "")
        else settings.MASTODON_CLIENT_SCOPE
    )


def _force_recreate_app(server_version):
    return re.match(r".+(Sharkey|Firefish).+", server_version or "")


def create_app(domain_name, server_version):
    url = "https://" + domain_name + API_CREATE_APP
    payload = {
        "client_name": settings.SITE_INFO["site_name"],
        "scopes": _get_scopes(server_version),
        "redirect_uris": _get_redirect_uris(server_version),
        "website": settings.SITE_INFO["site_url"],
    }
    response = post(url, data=payload, headers={"User-Agent": USER_AGENT})
    return response


def webfinger(site, username) -> dict | None:
    url = f"https://{site}/.well-known/webfinger?resource=acct:{username}@{site}"
    try:
        response = get(url, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            logger.warning(f"Error webfinger {username}@{site} {response.status_code}")
            return None
        j = response.json()
        return j
    except Exception:
        logger.warning(f"Error webfinger {username}@{site}")
        return None


def random_string_generator(n):
    s = string.ascii_letters + string.punctuation + string.digits
    return "".join(random.choice(s) for i in range(n))


def verify_account(site, token):
    url = "https://" + get_api_domain(site) + API_VERIFY_ACCOUNT
    try:
        response = get(
            url, headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}
        )
        return response.status_code, (
            response.json() if response.status_code == 200 else None
        )
    except Exception:
        return -1, None


def get_related_acct_list(site, token, api):
    url = "https://" + get_api_domain(site) + api
    results = []
    while url:
        try:
            response = get(
                url,
                headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"},
            )
            url = None
            if response.status_code == 200:
                r: list[dict[str, str]] = response.json()
                results.extend(
                    map(
                        lambda u: (
                            (  # type: ignore
                                u["acct"]
                                if u["acct"].find("@") != -1
                                else u["acct"] + "@" + site
                            )
                            if "acct" in u
                            else u
                        ),
                        r,
                    )
                )
                if "Link" in response.headers:
                    for ls in response.headers["Link"].split(","):
                        li = ls.strip().split(";")
                        if li[1].strip() == 'rel="next"':
                            url = li[0].strip().replace(">", "").replace("<", "")
        except Exception as e:
            logger.warning(f"Error GET {url} : {e}")
            url = None
    return results


def detect_server_info(login_domain: str) -> tuple[str, str, str]:
    url = f"https://{login_domain}/api/v1/instance"
    try:
        response = get(url, headers={"User-Agent": USER_AGENT})
    except Exception as e:
        logger.warning(f"Error connecting {login_domain}", extra={"exception": e})
        raise Exception(f"Error connecting to instance {login_domain}")
    if response.status_code != 200:
        logger.warning(f"Error in response from {login_domain} {response.status_code}")
        raise Exception(
            f"Instance {login_domain} returned error code {response.status_code}"
        )
    try:
        j = response.json()
        domain = j["uri"].lower().split("//")[-1].split("/")[0]
    except Exception as e:
        logger.warning(
            f"Error pasring response from {login_domain}", extra={"exception": e}
        )
        raise Exception(f"Instance {login_domain} returned invalid data")
    server_version = j["version"]
    api_domain = domain
    if domain != login_domain:
        url = f"https://{domain}/api/v1/instance"
        try:
            response = get(url, headers={"User-Agent": USER_AGENT})
            j = response.json()
        except Exception:
            api_domain = login_domain
    logger.info(
        f"detect_server_info: {login_domain} {domain} {api_domain} {server_version}"
    )
    return domain, api_domain, server_version


def verify_client(mast_app):
    payload = {
        "client_id": mast_app.client_id,
        "client_secret": mast_app.client_secret,
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "scope": _get_scopes(mast_app.server_version),
        "grant_type": "client_credentials",
    }
    headers = {"User-Agent": USER_AGENT}
    url = "https://" + (mast_app.api_domain or mast_app.domain_name) + API_OBTAIN_TOKEN
    try:
        response = post(
            url, data=payload, headers=headers, timeout=settings.MASTODON_TIMEOUT
        )
    except Exception as e:
        logger.warning(f"Error {url} {e}")
        return False
    if response.status_code != 200:
        logger.warning(f"Error {url} {response.status_code}")
        return False
    data = response.json()
    return data.get("access_token") is not None


def obtain_token(site, code, request):
    """Returns token if success else None."""
    mast_app = MastodonApplication.objects.get(domain_name=site)
    redirect_uri = request.build_absolute_uri(reverse("mastodon:oauth"))
    payload = {
        "client_id": mast_app.client_id,
        "client_secret": mast_app.client_secret,
        "redirect_uri": redirect_uri,
        "scope": _get_scopes(mast_app.server_version),
        "grant_type": "authorization_code",
        "code": code,
    }
    headers = {"User-Agent": USER_AGENT}
    auth = None
    if mast_app.is_proxy:
        url = "https://" + mast_app.proxy_to + API_OBTAIN_TOKEN
    else:
        url = (
            "https://"
            + (mast_app.api_domain or mast_app.domain_name)
            + API_OBTAIN_TOKEN
        )
    try:
        response = post(url, data=payload, headers=headers, auth=auth)
        if response.status_code != 200:
            logger.warning(
                f"Error {url} {payload} {response.status_code} {response.content}"
            )
            return None, None
    except Exception as e:
        logger.warning(f"Error {url} {e}")
        return None, None
    data = response.json()
    return data.get("access_token"), data.get("refresh_token", "")


def get_toot_visibility(visibility, user) -> TootVisibilityEnum:
    if visibility == 2:
        return TootVisibilityEnum.DIRECT
    elif visibility == 1:
        return TootVisibilityEnum.PRIVATE
    elif user.preference.post_public_mode == 0:
        return TootVisibilityEnum.PUBLIC
    else:
        return TootVisibilityEnum.UNLISTED


def get_or_create_fediverse_application(login_domain: str):
    domain = login_domain
    app = MastodonApplication.objects.filter(domain_name__iexact=domain).first()
    if not app:
        app = MastodonApplication.objects.filter(api_domain__iexact=domain).first()
    if app:
        if _force_recreate_app(app.server_version):
            logger.warning(f"Force recreate app for {login_domain}")
            data = create_app(app.api_domain, app.server_version).json()
            app.app_id = data["id"]
            app.client_id = data["client_id"]
            app.client_secret = data["client_secret"]
            app.vapid_key = data.get("vapid_key", "")
            app.save()
        return app
    if not settings.MASTODON_ALLOW_ANY_SITE:
        logger.warning(f"Disallowed to create app for {domain}")
        raise ValueError("Unsupported instance")
    if login_domain.lower() in settings.SITE_DOMAINS:
        raise ValueError("Unsupported instance")
    domain, api_domain, server_version = detect_server_info(login_domain)
    if (
        domain.lower() in settings.SITE_DOMAINS
        or api_domain.lower() in settings.SITE_DOMAINS
    ):
        raise ValueError("Unsupported instance")
    if "neodb/" in server_version:
        raise ValueError("Unsupported instance type")
    if login_domain != domain:
        app = MastodonApplication.objects.filter(domain_name__iexact=domain).first()
        if app:
            return app
    response = create_app(api_domain, server_version)
    if response.status_code != 200:
        logger.error(
            f"Error creating app for {domain} on {api_domain}: {response.status_code}"
        )
        raise Exception("Error creating app, code: " + str(response.status_code))
    try:
        data = response.json()
    except Exception:
        logger.error(f"Error creating app for {domain}: unable to parse response")
        raise Exception("Error creating app, invalid response")
    app = MastodonApplication.objects.create(
        domain_name=domain.lower(),
        api_domain=api_domain.lower(),
        server_version=server_version,
        app_id=data["id"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        vapid_key=data.get("vapid_key", ""),
    )
    # create a client token to avoid vacuum by Mastodon 4.2+
    try:
        verify_client(app)
    except Exception as e:
        logger.error(f"Error creating client token for {domain}", extra={"error": e})
    return app


def get_mastodon_login_url(app, login_domain, request):
    url = request.build_absolute_uri(reverse("mastodon:oauth"))
    scope = _get_scopes(app.server_version)
    return (
        "https://"
        + login_domain
        + "/oauth/authorize?client_id="
        + app.client_id
        + "&scope="
        + quote(scope)
        + "&redirect_uri="
        + url
        + "&response_type=code"
    )


class MastodonApplication(models.Model):
    domain_name = models.CharField(_("site domain name"), max_length=200, unique=True)
    api_domain = models.CharField(_("domain for api call"), max_length=200, blank=True)
    server_version = models.CharField(_("type and verion"), max_length=200, blank=True)
    app_id = models.CharField(_("in-site app id"), max_length=200)
    client_id = models.CharField(_("client id"), max_length=200)
    client_secret = models.CharField(_("client secret"), max_length=200)
    vapid_key = models.CharField(_("vapid key"), max_length=200, null=True, blank=True)
    star_mode = models.PositiveIntegerField(
        _("0: unicode moon; 1: custom emoji"), blank=False, default=0
    )
    max_status_len = models.PositiveIntegerField(
        _("max toot len"), blank=False, default=500
    )
    last_reachable_date = models.DateTimeField(null=True, default=None)
    disabled = models.BooleanField(default=False)
    is_proxy = models.BooleanField(default=False, blank=True)
    proxy_to = models.CharField(max_length=100, blank=True, default="")

    def __str__(self):
        return self.domain_name

    def detect_configurations(self):
        api_domain = self.api_domain or self.domain_name
        url = f"https://{api_domain}/api/v1/instance"
        response = get(url, headers={"User-Agent": settings.NEODB_USER_AGENT})
        if response.status_code == 200:
            j = response.json()
            max_chars = (
                j.get("configuration", {}).get("statuses", {}).get("max_characters")
            )
            if max_chars:
                self.max_status_len = max_chars
        url = f"https://{api_domain}/api/v1/custom_emojis"
        response = get(url, headers={"User-Agent": settings.NEODB_USER_AGENT})
        if response.status_code == 200:
            j = response.json()
            if next(filter(lambda e: e["shortcode"] == "star_half", j), None):
                self.star_mode = 1

    def verify(self):
        return verify_client(self)

    def refresh(self):
        response = create_app(self.api_domain, self.server_version)
        if response.status_code != 200:
            logger.error(
                f"Error creating app for {self.domain_name} on {self.api_domain}: {response.status_code}"
            )
            return False
        data = response.json()
        self.app_id = data["id"]
        self.client_id = data["client_id"]
        self.client_secret = data["client_secret"]
        self.vapid_key = data.get("vapid_key", "")
        self.save()
        logger.info(f"Refreshed {self.api_domain}")
        return True


class Mastodon:
    @staticmethod
    def get_sites():
        sites = cache.get(_sites_cache_key, [])
        if not sites:
            sites = list(
                MastodonAccount.objects.values("domain")
                .annotate(total=Count("domain"))
                .order_by("-total")
                .values_list("domain", flat=True)
            )
            cache.set(_sites_cache_key, sites, timeout=3600 * 8)

    @staticmethod
    def obtain_token(domain: str, code: str, request: HttpRequest):
        return obtain_token(domain, code, request)

    @staticmethod
    def generate_auth_url(domain: str, request):
        login_domain = (
            domain.strip().lower().split("//")[-1].split("/")[0].split("@")[-1]
        )
        app = get_or_create_fediverse_application(login_domain)
        if app.api_domain and app.api_domain != app.domain_name:
            login_domain = app.api_domain
        login_url = get_mastodon_login_url(app, login_domain, request)
        request.session["mastodon_domain"] = app.domain_name
        return login_url

    @staticmethod
    def authenticate(domain, access_token, refresh_token) -> "MastodonAccount | None":
        mastodon_account = MastodonAccount()
        mastodon_account.domain = domain
        mastodon_account.access_token = access_token
        mastodon_account.refresh_token = refresh_token
        if mastodon_account.refresh(save=False):
            existing_account = MastodonAccount.objects.filter(
                uid=mastodon_account.uid,
                domain=mastodon_account.domain,
            ).first()
            if not existing_account:
                existing_account = MastodonAccount.objects.filter(
                    handle=mastodon_account.handle,
                    domain=mastodon_account.domain,
                ).first()
                if existing_account:
                    # this is only needed if server is Firefish
                    logger.warning(
                        f"USER ID CHANGED: {existing_account.uid} -> {mastodon_account.uid} for {existing_account.handle}"
                    )
                    existing_account.uid = mastodon_account.uid
            if existing_account:
                existing_account.access_token = mastodon_account.access_token
                existing_account.refresh_token = mastodon_account.refresh_token
                existing_account.account_data = mastodon_account.account_data
                existing_account.save(update_fields=["access_data", "account_data"])
                return existing_account
            # for fresh account, ping them for convenience
            Takahe.fetch_remote_identity(mastodon_account.handle)
            return mastodon_account


class MastodonAccount(SocialAccount):
    class CrosspostMode(models.IntegerChoices):
        BOOST = 0, _("Boost")
        POST = 1, _("New Post")

    access_token = jsondata.EncryptedTextField(
        json_field_name="access_data", default=""
    )
    refresh_token = jsondata.EncryptedTextField(
        json_field_name="access_data", default=""
    )
    display_name = jsondata.CharField(json_field_name="account_data", default="")
    username = jsondata.CharField(json_field_name="account_data", default="")
    avatar = jsondata.CharField(json_field_name="account_data", default="")
    locked = jsondata.BooleanField(json_field_name="account_data", default=False)
    note = jsondata.CharField(json_field_name="account_data", default="")
    url = jsondata.CharField(json_field_name="account_data", default="")

    crosspost_mode = jsondata.IntegerField(
        json_field_name="preference_data", choices=CrosspostMode.choices, default=0
    )

    def webfinger(self) -> dict | None:
        acct = self.handle
        site = self.domain
        url = f"https://{site}/.well-known/webfinger?resource=acct:{acct}"
        try:
            response = get(url, headers={"User-Agent": settings.NEODB_USER_AGENT})
            if response.status_code != 200:
                logger.warning(f"Error webfinger {acct} {response.status_code}")
                return None
            j = response.json()
            return j
        except Exception:
            logger.warning(f"Error webfinger {acct}")
            return None

    @property
    def application(self) -> MastodonApplication | None:
        app = MastodonApplication.objects.filter(domain_name=self.domain).first()
        return app

    @functools.cached_property
    def _api_domain(self) -> str:
        app = self.application
        return app.api_domain if app else self.domain

    def rating_to_emoji(self, rating_grade: int | None) -> str:
        from journal.models.renderers import render_rating

        app = self.application
        return render_rating(rating_grade, app.star_mode if app else 0)

    def _get(self, url: str):
        url = url if url.startswith("https://") else f"https://{self._api_domain}{url}"
        headers = {
            "User-Agent": settings.NEODB_USER_AGENT,
            "Authorization": f"Bearer {self.access_token}",
        }
        return get(url, headers=headers)

    def _post(self, url: str, data, files=None):
        url = url if url.startswith("https://") else f"https://{self._api_domain}{url}"
        return post(
            url,
            data=data,
            files=files,
            headers={
                "User-Agent": settings.NEODB_USER_AGENT,
                "Authorization": f"Bearer {self.access_token}",
                "Idempotency-Key": random_string_generator(16),
            },
        )

    def _delete(self, url: str, data, files=None):
        url = url if url.startswith("https://") else f"https://{self._api_domain}{url}"
        return delete(
            url,
            headers={
                "User-Agent": settings.NEODB_USER_AGENT,
                "Authorization": f"Bearer {self.access_token}",
            },
        )

    def _put(self, url: str, data, files=None):
        url = url if url.startswith("https://") else f"https://{self._api_domain}{url}"
        return put(
            url,
            data=data,
            files=files,
            headers={
                "User-Agent": settings.NEODB_USER_AGENT,
                "Authorization": f"Bearer {self.access_token}",
                "Idempotency-Key": random_string_generator(16),
            },
        )

    def verify_account(self):
        try:
            response = self._get("/api/v1/accounts/verify_credentials")
            return response.status_code, (
                response.json() if response.status_code == 200 else None
            )
        except Exception:
            return -1, None

    def get_related_accounts(self, api_path):
        if api_path in ["followers", "following"]:
            url = f"/api/v1/accounts/{self.account_data['id']}/{api_path}"
        else:
            url = f"/api/v1/{api_path}"
        results = []
        while url:
            try:
                response = self._get(url)
                url = None
                if response.status_code == 200:
                    r: list[dict[str, str]] = response.json()
                    results.extend(
                        map(
                            lambda u: (
                                (
                                    u["acct"]
                                    if u["acct"].find("@") != -1
                                    else u["acct"] + "@" + self.domain
                                )
                                if "acct" in u
                                else u
                            ),
                            r,
                        )
                    )
                    if "Link" in response.headers:
                        for ls in response.headers["Link"].split(","):
                            li = ls.strip().split(";")
                            if li[1].strip() == 'rel="next"':
                                url = li[0].strip().replace(">", "").replace("<", "")
            except Exception as e:
                logger.warning(f"Error GET {url} : {e}")
                url = None
        return results

    def check_alive(self, save=True):
        self.last_refresh = timezone.now()
        if not self.webfinger():
            logger.warning(f"Unable to fetch web finger for {self}")
            return False
        self.last_reachable = timezone.now()
        if save:
            self.save(update_fields=["last_reachable"])
        return True

    def refresh(self, save=True):
        code, mastodon_account = self.verify_account()
        self.last_refresh = timezone.now()
        if code == 401:
            logger.warning(f"Refresh mastodon data error 401 for {self}")
            # self.access_token = ""
            # if save:
            #     self.save(update_fields=["access_data"])
            return False
        if not mastodon_account:
            logger.warning(f"Refresh mastodon data error {code} for {self}")
            return False
        handle = f"{mastodon_account['username']}@{self.domain}"
        uid = mastodon_account["id"]
        if self.uid != uid:
            if self.uid:
                logger.warning(f"user id changed {self.uid} -> {uid}")
            self.uid = uid
        if self.handle != handle:
            if self.handle:
                logger.warning(f"username changed {self.handle} -> {handle}")
            self.handle = handle
        self.account_data = mastodon_account
        if save:
            self.save(update_fields=["uid", "handle", "account_data", "last_refresh"])
        return True

    def refresh_graph(self, save=True):
        self.followers = self.get_related_accounts("followers")
        self.following = self.get_related_accounts("following")
        self.mutes = self.get_related_accounts("mutes")
        self.blocks = self.get_related_accounts("blocks")
        self.domain_blocks = self.get_related_accounts("domain_blocks")
        if save:
            self.save(
                update_fields=[
                    "followers",
                    "following",
                    "mutes",
                    "blocks",
                    "domain_blocks",
                ]
            )
        return True

    def sync_graph(self):
        c = 0

        def get_identity_ids(accts: list):
            return set(
                MastodonAccount.objects.filter(handle__in=accts).values_list(
                    "user__identity", flat=True
                )
            )

        def get_identity_ids_in_domains(domains: list):
            return set(
                MastodonAccount.objects.filter(domain__in=domains).values_list(
                    "user__identity", flat=True
                )
            )

        me = self.user.identity.pk
        for target_identity in get_identity_ids(self.following):
            if not Takahe.get_is_following(me, target_identity):
                Takahe.follow(me, target_identity, True)
                c += 1

        for target_identity in get_identity_ids(self.blocks):
            if not Takahe.get_is_blocking(me, target_identity):
                Takahe.block(me, target_identity)
                c += 1

        for target_identity in get_identity_ids_in_domains(self.domain_blocks):
            if not Takahe.get_is_blocking(me, target_identity):
                Takahe.block(me, target_identity)
                c += 1

        for target_identity in get_identity_ids(self.mutes):
            if not Takahe.get_is_muting(me, target_identity):
                Takahe.mute(me, target_identity)
                c += 1

        return c

    def boost(self, post_url: str):
        boost_toot(self._api_domain, self.access_token, post_url)

    def boost_later(self, post_url: str):
        django_rq.get_queue("fetch").enqueue(
            boost_toot, self._api_domain, self.access_token, post_url
        )

    def delete_post(self, post_id: str):
        delete_toot(self._api_domain, self.access_token, post_id)

    def delete_post_later(self, post_id: str):
        django_rq.get_queue("fetch").enqueue(
            delete_toot, self._api_domain, self.access_token, post_id
        )

    def post(
        self,
        content: str,
        visibility: "VisibilityType",
        update_id: str | None = None,
        reply_to_id: str | None = None,
        sensitive: bool = False,
        spoiler_text: str | None = None,
        attachments: list = [],
        obj: "Item | Content | None" = None,
        rating: int | None = None,
    ) -> dict:
        v = get_toot_visibility(visibility, self.user)
        text = (
            content.replace("##rating##", self.rating_to_emoji(rating))
            .replace("##obj_link_if_plain##", obj.absolute_url + "\n" if obj else "")
            .replace("##obj##", obj.display_title if obj else "")
        )
        response = post_toot2(
            self._api_domain,
            self.access_token,
            text,
            v,
            update_id,
            reply_to_id,
            sensitive,
            spoiler_text,
            attachments,
        )
        if response is not None:
            if response.status_code in [200, 201]:
                j = response.json()
                return {"id": j["id"], "url": j["url"]}
            elif response.status_code == 401:
                raise PermissionDenied()
        raise RequestAborted()

    def get_reauthorize_url(self):
        return reverse("mastodon:login") + "?domain=" + self.domain
