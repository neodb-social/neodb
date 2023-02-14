import requests
import string
import random
import functools
import logging
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from django.urls import reverse
from urllib.parse import quote
from .models import CrossSiteUserInfo, MastodonApplication
from mastodon.utils import rating_to_emoji
import re

logger = logging.getLogger(__name__)

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

TWITTER_DOMAIN = "twitter.com"

TWITTER_API_ME = "https://api.twitter.com/2/users/me"

TWITTER_API_POST = "https://api.twitter.com/2/tweets"

TWITTER_API_TOKEN = "https://api.twitter.com/2/oauth2/token"

USER_AGENT = f"{settings.CLIENT_NAME}/1.0"

get = functools.partial(requests.get, timeout=settings.MASTODON_TIMEOUT)
put = functools.partial(requests.put, timeout=settings.MASTODON_TIMEOUT)
post = functools.partial(requests.post, timeout=settings.MASTODON_TIMEOUT)


def get_api_domain(domain):
    app = MastodonApplication.objects.filter(domain_name=domain).first()
    return app.api_domain if app and app.api_domain else domain


# low level api below


def post_toot(
    site,
    content,
    visibility,
    token,
    local_only=False,
    update_id=None,
    spoiler_text=None,
):
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": random_string_generator(16),
    }
    response = None
    if site == TWITTER_DOMAIN:
        url = TWITTER_API_POST
        payload = {"text": content if len(content) <= 150 else content[0:150] + "..."}
        response = post(url, headers=headers, json=payload)
        if response.status_code == 201:
            response.status_code = 200
        if response.status_code != 200:
            logger.error(f"Error {url} {response.status_code}")
    else:
        url = "https://" + get_api_domain(site) + API_PUBLISH_TOOT
        payload = {
            "status": content,
            "visibility": visibility,
        }
        if spoiler_text:
            payload["spoiler_text"] = spoiler_text
        if local_only:
            payload["local_only"] = True
        try:
            if update_id:
                response = put(url + "/" + update_id, headers=headers, data=payload)
            if update_id is None or response.status_code != 200:
                headers["Idempotency-Key"] = random_string_generator(16)
                response = post(url, headers=headers, data=payload)
            if response.status_code == 201:
                response.status_code = 200
            if response.status_code != 200:
                logger.error(f"Error {url} {response.status_code}")
        except Exception:
            response = None
    return response


def create_app(domain_name):
    # naive protocal strip
    is_http = False
    if domain_name.startswith("https://"):
        domain_name = domain_name.replace("https://", "")
    elif domain_name.startswith("http://"):
        is_http = True
        domain_name = domain_name.replace("http://", "")
    if domain_name.endswith("/"):
        domain_name = domain_name[0:-1]

    if not is_http:
        url = "https://" + domain_name + API_CREATE_APP
    else:
        url = "http://" + domain_name + API_CREATE_APP

    payload = {
        "client_name": settings.CLIENT_NAME,
        "scopes": settings.MASTODON_CLIENT_SCOPE,
        "redirect_uris": settings.REDIRECT_URIS,
        "website": settings.APP_WEBSITE,
    }

    response = post(url, data=payload, headers={"User-Agent": USER_AGENT})
    return response


# utils below
def random_string_generator(n):
    s = string.ascii_letters + string.punctuation + string.digits
    return "".join(random.choice(s) for i in range(n))


def verify_account(site, token):
    if site == TWITTER_DOMAIN:
        url = (
            TWITTER_API_ME
            + "?user.fields=id,username,name,description,profile_image_url,created_at,protected"
        )
        try:
            response = get(
                url,
                headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"},
            )
            if response.status_code != 200:
                logger.error(f"Error {url} {response.status_code}")
                return response.status_code, None
            r = response.json()["data"]
            r["display_name"] = r["name"]
            r["note"] = r["description"]
            r["avatar"] = r["profile_image_url"]
            r["avatar_static"] = r["profile_image_url"]
            r["locked"] = r["protected"]
            r["url"] = f'https://{TWITTER_DOMAIN}/{r["username"]}'
            return 200, r
        except Exception:
            return -1, None
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
    if site == TWITTER_DOMAIN:
        return []
    url = "https://" + get_api_domain(site) + api
    results = []
    while url:
        response = get(
            url, headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}
        )
        url = None
        if response.status_code == 200:
            results.extend(
                map(
                    lambda u: (
                        u["acct"]
                        if u["acct"].find("@") != -1
                        else u["acct"] + "@" + site
                    )
                    if "acct" in u
                    else u,
                    response.json(),
                )
            )
            if "Link" in response.headers:
                for ls in response.headers["Link"].split(","):
                    li = ls.strip().split(";")
                    if li[1].strip() == 'rel="next"':
                        url = li[0].strip().replace(">", "").replace("<", "")
    return results


class TootVisibilityEnum:
    PUBLIC = "public"
    PRIVATE = "private"
    DIRECT = "direct"
    UNLISTED = "unlisted"


def get_mastodon_application(login_domain):
    domain = login_domain
    api_domain = ""
    server_version = ""
    app = MastodonApplication.objects.filter(domain_name=domain).first()
    if not app:
        # detect the correct domains
        url = f"https://{login_domain}/api/v1/instance"
        try:
            response = get(url, headers={"User-Agent": USER_AGENT})
            if response.status_code != 200:
                logger.error(f"Error connecting {domain}: {response.status_code}")
                return None, "实例连接错误，代码: " + str(response.status_code)
            j = response.json()
            domain = j["uri"].lower().split("//")[-1].split("/")[0]
            api_domain = domain
            if "urls" in j and "streaming_api" in j["urls"]:
                api_domain = j["urls"]["streaming_api"].split("://")[1]
            server_version = j["version"]
        except (requests.exceptions.Timeout, ConnectionError):
            logger.error(f"Error connecting {login_domain}: Timeout")
            return None, "连接实例请求超时"
        except Exception as e:
            logger.error(f"Error connecting {login_domain}: {e}")
            return None, "无法识别实例信息"

    app = MastodonApplication.objects.filter(domain_name=domain).first()
    if app is not None:
        return app, ""
    if domain == TWITTER_DOMAIN:
        return None, "Twitter未配置"
    error_msg = None
    try:
        response = create_app(api_domain)
    except (requests.exceptions.Timeout, ConnectionError):
        error_msg = "联邦网络请求超时。"
        logger.error(f"Error creating app for {domain} on {api_domain}: Timeout")
    except Exception as e:
        error_msg = "联邦网络请求失败 " + str(e)
        logger.error(f"Error creating app for {domain} on {api_domain}: {e}")
    else:
        # fill the form with returned data
        if response.status_code != 200:
            error_msg = "实例连接错误，代码: " + str(response.status_code)
            logger.error(
                f"Error creating app for {domain} on {api_domain}: {response.status_code}"
            )
        else:
            try:
                data = response.json()
            except Exception:
                error_msg = "实例返回内容无法识别"
                logger.error(
                    f"Error creating app for {domain}: unable to parse response"
                )
            else:
                if settings.MASTODON_ALLOW_ANY_SITE:
                    app = MastodonApplication.objects.create(
                        domain_name=domain,
                        api_domain=api_domain,
                        server_version=server_version,
                        app_id=data["id"],
                        client_id=data["client_id"],
                        client_secret=data["client_secret"],
                        vapid_key=data["vapid_key"] if "vapid_key" in data else "",
                    )
                else:
                    error_msg = "不支持其它实例登录"
                    logger.error(f"Disallowed to create app for {domain}")
    return app, error_msg


def get_mastodon_login_url(app, login_domain, request):
    url = request.scheme + "://" + request.get_host() + reverse("users:OAuth2_login")
    if login_domain == TWITTER_DOMAIN:
        return f"https://twitter.com/i/oauth2/authorize?response_type=code&client_id={app.client_id}&redirect_uri={quote(url)}&scope={quote(settings.TWITTER_CLIENT_SCOPE)}&state=state&code_challenge=challenge&code_challenge_method=plain"
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


def obtain_token(site, request, code):
    """Returns token if success else None."""
    mast_app = MastodonApplication.objects.get(domain_name=site)
    redirect_uri = (
        request.scheme + "://" + request.get_host() + reverse("users:OAuth2_login")
    )
    payload = {
        "client_id": mast_app.client_id,
        "client_secret": mast_app.client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code": code,
    }
    headers = {"User-Agent": USER_AGENT}
    auth = None
    if mast_app.is_proxy:
        url = "https://" + mast_app.proxy_to + API_OBTAIN_TOKEN
    elif site == TWITTER_DOMAIN:
        url = TWITTER_API_TOKEN
        auth = (mast_app.client_id, mast_app.client_secret)
        del payload["client_secret"]
        payload["code_verifier"] = "challenge"
    else:
        url = (
            "https://"
            + (mast_app.api_domain or mast_app.domain_name)
            + API_OBTAIN_TOKEN
        )
    try:
        response = post(url, data=payload, headers=headers, auth=auth)
        # {"token_type":"bearer","expires_in":7200,"access_token":"VGpkOEZGR3FQRDJ5NkZ0dmYyYWIwS0dqeHpvTnk4eXp0NV9nWDJ2TEpmM1ZTOjE2NDg3ODMxNTU4Mzc6MToxOmF0OjE","scope":"block.read follows.read offline.access tweet.write users.read mute.read","refresh_token":"b1pXbGEzeUF1WE5yZHJOWmxTeWpvMTBrQmZPd0czLU0tQndZQTUyU3FwRDVIOjE2NDg3ODMxNTU4Mzg6MToxOnJ0OjE"}
        if response.status_code != 200:
            logger.error(f"Error {url} {response.status_code}")
            return None, None
    except Exception as e:
        logger.error(f"Error {url} {e}")
        return None, None
    data = response.json()
    return data.get("access_token"), data.get("refresh_token", "")


def refresh_access_token(site, refresh_token):
    if site != TWITTER_DOMAIN:
        return None
    mast_app = MastodonApplication.objects.get(domain_name=site)
    url = TWITTER_API_TOKEN
    payload = {
        "client_id": mast_app.client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    headers = {"User-Agent": USER_AGENT}
    auth = (mast_app.client_id, mast_app.client_secret)
    response = post(url, data=payload, headers=headers, auth=auth)
    if response.status_code != 200:
        logger.error(f"Error {url} {response.status_code}")
        return None
    data = response.json()
    return data.get("access_token")


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
        spoiler_text = f"关于《{item.title}》 可能有关键情节等敏感内容"
        return spoiler_text, text.replace(">!", "").replace("!<", "")
    else:
        return None, text


def get_visibility(visibility, user):
    if visibility == 2:
        return TootVisibilityEnum.DIRECT
    elif visibility == 1:
        return TootVisibilityEnum.PRIVATE
    elif user.get_preference().mastodon_publish_public:
        return TootVisibilityEnum.PUBLIC
    else:
        return TootVisibilityEnum.UNLISTED


def share_mark(mark):
    from catalog.common import ItemCategory

    user = mark.owner
    if mark.visibility == 2:
        visibility = TootVisibilityEnum.DIRECT
    elif mark.visibility == 1:
        visibility = TootVisibilityEnum.PRIVATE
    elif user.get_preference().mastodon_publish_public:
        visibility = TootVisibilityEnum.PUBLIC
    else:
        visibility = TootVisibilityEnum.UNLISTED
    tags = (
        "\n"
        + user.get_preference().mastodon_append_tag.replace(
            "[category]", str(ItemCategory(mark.item.category).label)
        )
        if user.get_preference().mastodon_append_tag
        else ""
    )
    stars = rating_to_emoji(
        mark.rating,
        MastodonApplication.objects.get(domain_name=user.mastodon_site).star_mode,
    )
    content = f"{mark.translated_status}《{mark.item.title}》{stars}\n{mark.item.absolute_url}\n{mark.text or ''}{tags}"
    update_id = get_status_id_by_url(mark.shared_link)
    spoiler_text, content = get_spoiler_text(content, mark.item)
    response = post_toot(
        user.mastodon_site,
        content,
        visibility,
        user.mastodon_token,
        False,
        update_id,
        spoiler_text,
    )
    if response and response.status_code in [200, 201]:
        j = response.json()
        if "url" in j:
            mark.shared_link = j["url"]
        elif "data" in j:
            mark.shared_link = (
                f"https://twitter.com/{user.username}/status/{j['data']['id']}"
            )
        if mark.shared_link:
            mark.save(update_fields=["shared_link"])
        return True
    else:
        logger.error(response)
        return False


def share_review(review):
    from catalog.common import ItemCategory

    user = review.owner
    if review.visibility == 2:
        visibility = TootVisibilityEnum.DIRECT
    elif review.visibility == 1:
        visibility = TootVisibilityEnum.PRIVATE
    elif user.get_preference().mastodon_publish_public:
        visibility = TootVisibilityEnum.PUBLIC
    else:
        visibility = TootVisibilityEnum.UNLISTED
    tags = (
        "\n"
        + user.get_preference().mastodon_append_tag.replace(
            "[category]", str(ItemCategory(review.item.category).label)
        )
        if user.get_preference().mastodon_append_tag
        else ""
    )
    content = (
        f"发布了关于《{review.item.title}》的评论\n{review.absolute_url}\n{review.title}{tags}"
    )
    update_id = None
    if review.shared_link:  # "https://mastodon.social/@username/1234567890"
        r = re.match(
            r".+/(\w+)$", review.shared_link
        )  # might be re.match(r'.+/([^/]+)$', u) if Pleroma supports edit
        update_id = r[1] if r else None
    response = post_toot(
        user.mastodon_site, content, visibility, user.mastodon_token, False, update_id
    )
    if response and response.status_code in [200, 201]:
        j = response.json()
        if "url" in j:
            review.shared_link = j["url"]
        elif "data" in j:
            review.shared_link = (
                f"https://twitter.com/{user.username}/status/{j['data']['id']}"
            )
        if review.shared_link:
            review.save(update_fields=["shared_link"])
        return True
    else:
        return False


def share_collection(collection, comment, user, visibility_no):
    if visibility_no == 2:
        visibility = TootVisibilityEnum.DIRECT
    elif visibility_no == 1:
        visibility = TootVisibilityEnum.PRIVATE
    elif user.get_preference().mastodon_publish_public:
        visibility = TootVisibilityEnum.PUBLIC
    else:
        visibility = TootVisibilityEnum.UNLISTED
    tags = (
        "\n" + user.get_preference().mastodon_append_tag.replace("[category]", "收藏单")
        if user.get_preference().mastodon_append_tag
        else ""
    )
    user_str = (
        "我"
        if user == collection.owner
        else " @" + collection.owner.mastodon_username + " "
    )
    content = f"分享{user_str}的收藏单《{collection.title}》\n{collection.absolute_url}\n{comment}{tags}"
    response = post_toot(user.mastodon_site, content, visibility, user.mastodon_token)
    if response and response.status_code in [200, 201]:
        return True
    else:
        return False
