import time

from django.urls import reverse
from loguru import logger

from catalog.common import CachedDownloader
from users.models import APIdentity


def _get_ap_object_type(typ: str | None) -> str | None:
    actor_types = ["person", "service", "application", "group", "organization"]
    post_types = [
        "note",
        "article",
        "post",
        "question",
        "event",
        "video",
        "audio",
        "image",
    ]
    if not typ:
        return
    typ = typ.lower()
    if typ in actor_types:
        return "identity"
    elif typ in post_types:
        return "post"


def _get_local_url_for_ap_identity(uri):
    from takahe.models import Identity

    i = Identity.objects.filter(actor_uri=uri).first()
    if i:
        ii = APIdentity.by_takahe_identity(i)
        if ii:
            return ii.url


def _get_local_url_for_ap_post(uri):
    from takahe.models import Post

    p = Post.objects.filter(object_uri=uri).first()
    if p:
        ii = APIdentity.objects.filter(pk=p.author_id).first()
        if ii:
            return reverse("journal:post_view", args=(ii.handle, p.pk))


def search_by_ap_url(url, fetcher, max_retries=15) -> str | None:
    from catalog.sites.fedi import FediverseInstance
    from takahe.models import InboxMessage

    headers = FediverseInstance.request_header

    try:
        j = CachedDownloader(url, headers=headers, timeout=2).download().json()
    except Exception:
        return
    typ = j.get("type")
    uri = j.get("id", "")
    tries = max_retries
    typ = _get_ap_object_type(typ)
    if not typ or not uri:
        return
    logger.debug(f"Detected ap {typ} {uri}")
    m = {"type": "searchurl", "url": url}
    if fetcher:
        m["handle"] = fetcher.handle
    InboxMessage.create_internal(m)
    while tries > 0:
        tries -= 1
        if typ == "identity":
            u = _get_local_url_for_ap_identity(uri)
        else:
            u = _get_local_url_for_ap_post(uri)
        if u:
            return u
        if tries == 0:
            logger.debug(f"Waiting for {uri} timeout")
            return
        time.sleep(1)
