import functools
import random
import re
import string
import time
from urllib.parse import quote

import django_rq
import requests
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _
from loguru import logger

from mastodon.utils import rating_to_emoji

from .models import MastodonApplication

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

get = functools.partial(requests.get, timeout=settings.MASTODON_TIMEOUT)
put = functools.partial(requests.put, timeout=settings.MASTODON_TIMEOUT)
post = functools.partial(requests.post, timeout=settings.MASTODON_TIMEOUT)


def get_api_domain(domain):
    app = MastodonApplication.objects.filter(domain_name=domain).first()
    return app.api_domain if app and app.api_domain else domain


# low level api below


def boost_toot(site, token, toot_url):
    domain = get_api_domain(site)
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


def boost_toot_later(user, post_url):
    if user and user.mastodon_token and user.mastodon_site and post_url:
        django_rq.get_queue("fetch").enqueue(
            boost_toot, user.mastodon_site, user.mastodon_token, post_url
        )


def post_toot_later(
    user,
    content,
    visibility,
    local_only=False,
    update_id=None,
    spoiler_text=None,
    img=None,
    img_name=None,
    img_type=None,
):
    if user and user.mastodon_token and user.mastodon_site and content:
        django_rq.get_queue("fetch").enqueue(
            post_toot,
            user.mastodon_site,
            content,
            visibility,
            user.mastodon_token,
            local_only,
            update_id,
            spoiler_text,
            img,
            img_name,
            img_type,
        )


def post_toot(
    site,
    content,
    visibility,
    token,
    local_only=False,
    update_id=None,
    spoiler_text=None,
    img=None,
    img_name=None,
    img_type=None,
):
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": random_string_generator(16),
    }
    media_id = None
    if img and img_name and img_type:
        try:
            media_id = (
                requests.post(
                    "https://" + get_api_domain(site) + "/api/v1/media",
                    headers=headers,
                    data={},
                    files={"file": (img_name, img, img_type)},
                )
                .json()
                .get("id")
            )
            ready = False
            while ready is False:
                time.sleep(3)
                j = requests.get(
                    "https://" + get_api_domain(site) + "/api/v1/media/" + media_id,
                    headers=headers,
                ).json()
                ready = j.get("url") is not None
        except Exception as e:
            logger.warning(f"Error uploading image {e}")
        headers["Idempotency-Key"] = random_string_generator(16)
    response = None
    url = "https://" + get_api_domain(site) + API_PUBLISH_TOOT
    payload = {
        "status": content,
        "visibility": visibility,
    }
    if media_id:
        payload["media_ids[]"] = [media_id]
    if spoiler_text:
        payload["spoiler_text"] = spoiler_text
    if local_only:
        payload["local_only"] = True
    try:
        if update_id:
            response = put(url + "/" + update_id, headers=headers, data=payload)
        if not update_id or (response is not None and response.status_code != 200):
            headers["Idempotency-Key"] = random_string_generator(16)
            response = post(url, headers=headers, data=payload)
        if response is not None and response.status_code == 201:
            response.status_code = 200
        if response is not None and response.status_code != 200:
            logger.warning(f"Error {url} {response.status_code}")
    except Exception as e:
        logger.warning(f"Error posting {e}")
        response = None
    return response


def delete_toot(user, toot_url):
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {user.mastodon_token}",
        "Idempotency-Key": random_string_generator(16),
    }
    toot_id = get_status_id_by_url(toot_url)
    url = (
        "https://"
        + get_api_domain(user.mastodon_site)
        + API_PUBLISH_TOOT
        + "/"
        + toot_id
    )
    try:
        response = requests.delete(url, headers=headers)
        if response.status_code != 200:
            logger.warning(f"Error DELETE {url} {response.status_code}")
    except Exception as e:
        logger.warning(f"Error deleting {e}")


def delete_toot_later(user, toot_url):
    if user and user.mastodon_token and user.mastodon_site and toot_url:
        django_rq.get_queue("fetch").enqueue(delete_toot, user, toot_url)


def post_toot2(
    user,
    content,
    visibility,
    update_toot_url: str | None = None,
    reply_to_toot_url: str | None = None,
    sensitive: bool = False,
    spoiler_text: str | None = None,
    attachments: list = [],
):
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {user.mastodon_token}",
        "Idempotency-Key": random_string_generator(16),
    }
    base_url = "https://" + get_api_domain(user.mastodon_site)
    response = None
    url = base_url + API_PUBLISH_TOOT
    payload = {
        "status": content,
        "visibility": get_toot_visibility(visibility, user),
    }
    update_id = get_status_id_by_url(update_toot_url)
    reply_to_id = get_status_id_by_url(reply_to_toot_url)
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
                requests.post(
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


def _get_redirect_uris(allow_multiple=True) -> str:
    u = settings.SITE_INFO["site_url"] + "/account/login/oauth"
    if not allow_multiple:
        return u
    u2s = [f"https://{d}/account/login/oauth" for d in settings.ALTERNATIVE_DOMAINS]
    return "\n".join([u] + u2s)


def create_app(domain_name, allow_multiple_redir):
    url = "https://" + domain_name + API_CREATE_APP
    payload = {
        "client_name": settings.SITE_INFO["site_name"],
        "scopes": settings.MASTODON_CLIENT_SCOPE,
        "redirect_uris": _get_redirect_uris(allow_multiple_redir),
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


# utils below
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


class TootVisibilityEnum:
    PUBLIC = "public"
    PRIVATE = "private"
    DIRECT = "direct"
    UNLISTED = "unlisted"


def detect_server_info(login_domain: str) -> tuple[str, str, str]:
    url = f"https://{login_domain}/api/v1/instance"
    try:
        response = get(url, headers={"User-Agent": USER_AGENT})
    except Exception as e:
        logger.error(f"Error connecting {login_domain}", extra={"exception": e})
        raise Exception(f"Error connecting to instance {login_domain}")
    if response.status_code != 200:
        logger.error(f"Error connecting {login_domain}", extra={"response": response})
        raise Exception(
            f"Instance {login_domain} returned error code {response.status_code}"
        )
    try:
        j = response.json()
        domain = j["uri"].lower().split("//")[-1].split("/")[0]
    except Exception as e:
        logger.error(f"Error connecting {login_domain}", extra={"exception": e})
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


def get_or_create_fediverse_application(login_domain):
    domain = login_domain
    app = MastodonApplication.objects.filter(domain_name__iexact=domain).first()
    if not app:
        app = MastodonApplication.objects.filter(api_domain__iexact=domain).first()
    if app:
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
    allow_multiple_redir = True
    if "; Pixelfed" in server_version or server_version.startswith("0."):
        # Pixelfed and GoToSocial don't support multiple redirect uris
        allow_multiple_redir = False
    response = create_app(api_domain, allow_multiple_redir)
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
    url = request.build_absolute_uri(reverse("users:login_oauth"))
    version = app.server_version or ""
    scope = (
        settings.MASTODON_LEGACY_CLIENT_SCOPE
        if "Pixelfed" in version
        else settings.MASTODON_CLIENT_SCOPE
    )
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


def verify_client(mast_app):
    payload = {
        "client_id": mast_app.client_id,
        "client_secret": mast_app.client_secret,
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "scope": settings.MASTODON_CLIENT_SCOPE,
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


def obtain_token(site, request, code):
    """Returns token if success else None."""
    mast_app = MastodonApplication.objects.get(domain_name=site)
    redirect_uri = request.build_absolute_uri(reverse("users:login_oauth"))
    payload = {
        "client_id": mast_app.client_id,
        "client_secret": mast_app.client_secret,
        "redirect_uri": redirect_uri,
        "scope": settings.MASTODON_CLIENT_SCOPE,
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
            logger.warning(f"Error {url} {response.status_code}")
            return None, None
    except Exception as e:
        logger.warning(f"Error {url} {e}")
        return None, None
    data = response.json()
    return data.get("access_token"), data.get("refresh_token", "")


def revoke_token(site, token):
    mast_app = MastodonApplication.objects.get(domain_name=site)

    payload = {
        "client_id": mast_app.client_id,
        "client_secret": mast_app.client_secret,
        "token": token,
    }

    if mast_app.is_proxy:
        url = "https://" + mast_app.proxy_to + API_REVOKE_TOKEN
    else:
        url = "https://" + get_api_domain(site) + API_REVOKE_TOKEN
    post(url, data=payload, headers={"User-Agent": USER_AGENT})


def get_status_id_by_url(url):
    if not url:
        return None
    r = re.match(
        r".+/(\w+)$", url
    )  # might be re.match(r'.+/([^/]+)$', u) if Pleroma supports edit
    return r[1] if r else None


def get_spoiler_text(text, item):
    if text.find(">!") != -1:
        spoiler_text = _(
            "regarding {item_title}, may contain spoiler or triggering content"
        ).format(item_title=item.display_title)
        return spoiler_text, text.replace(">!", "").replace("!<", "")
    else:
        return None, text


def get_toot_visibility(visibility, user):
    if visibility == 2:
        return TootVisibilityEnum.DIRECT
    elif visibility == 1:
        return TootVisibilityEnum.PRIVATE
    elif user.preference.post_public_mode == 0:
        return TootVisibilityEnum.PUBLIC
    else:
        return TootVisibilityEnum.UNLISTED


def share_comment(comment):
    from catalog.common import ItemCategory
    from journal.models import ShelfManager, ShelfType

    user = comment.owner.user
    visibility = get_toot_visibility(comment.visibility, user)
    tags = (
        "\n"
        + user.preference.mastodon_append_tag.replace(
            "[category]", str(ItemCategory(comment.item.category).label)
        )
        if user.preference.mastodon_append_tag
        else ""
    )
    spoiler_text, txt = get_spoiler_text(comment.text or "", comment.item)
    tpl = ShelfManager.get_action_template(ShelfType.PROGRESS, comment.item.category)
    content = (
        _(tpl).format(item=comment.item.display_title)
        + f"\n{txt}\n{comment.item.absolute_url}{tags}"
    )
    update_id = None
    if comment.metadata.get(
        "shared_link"
    ):  # "https://mastodon.social/@username/1234567890"
        r = re.match(
            r".+/(\w+)$", comment.metadata.get("shared_link")
        )  # might be re.match(r'.+/([^/]+)$', u) if Pleroma supports edit
        update_id = r[1] if r else None
    response = post_toot(
        user.mastodon_site,
        content,
        visibility,
        user.mastodon_token,
        False,
        update_id,
        spoiler_text,
    )
    if response is not None and response.status_code in [200, 201]:
        j = response.json()
        if "url" in j:
            comment.metadata["shared_link"] = j["url"]
            comment.save()
        return True
    else:
        return False


def share_mark(mark, post_as_new=False):
    from catalog.common import ItemCategory

    user = mark.owner.user
    visibility = get_toot_visibility(mark.visibility, user)
    site = MastodonApplication.objects.filter(domain_name=user.mastodon_site).first()
    stars = rating_to_emoji(
        mark.rating_grade,
        site.star_mode if site else 0,
    )
    spoiler_text, txt = get_spoiler_text(mark.comment_text or "", mark.item)
    content = f"{mark.get_action_for_feed()} {stars}\n{mark.item.absolute_url}\n{txt}{mark.tag_text}"
    update_id = (
        None
        if post_as_new
        else get_status_id_by_url((mark.shelfmember.metadata or {}).get("shared_link"))
    )
    response = post_toot(
        user.mastodon_site,
        content,
        visibility,
        user.mastodon_token,
        False,
        update_id,
        spoiler_text,
    )
    if response is not None and response.status_code in [200, 201]:
        j = response.json()
        if "url" in j:
            mark.shelfmember.metadata = {"shared_link": j["url"]}
            mark.shelfmember.save(update_fields=["metadata"])
        return True, 200
    else:
        logger.warning(response)
        return False, response.status_code if response is not None else -1


def share_collection(collection, comment, user, visibility_no, link):
    visibility = get_toot_visibility(visibility_no, user)
    tags = (
        "\n"
        + user.preference.mastodon_append_tag.replace("[category]", _("collection"))
        if user.preference.mastodon_append_tag
        else ""
    )
    user_str = (
        _("shared my collection")
        if user == collection.owner.user
        else (
            _("shared {username}'s collection").format(
                username=(
                    " @" + collection.owner.user.mastodon_acct + " "
                    if collection.owner.user.mastodon_acct
                    else " " + collection.owner.username + " "
                )
            )
        )
    )
    content = f"{user_str}:{collection.title}\n{link}\n{comment}{tags}"
    response = post_toot(user.mastodon_site, content, visibility, user.mastodon_token)
    if response is not None and response.status_code in [200, 201]:
        return True
    else:
        return False
