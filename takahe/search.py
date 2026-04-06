import time

from django.urls import reverse
from loguru import logger

from users.models import APIdentity


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


def _get_local_url(url: str) -> str | None:
    return _get_local_url_for_ap_identity(url) or _get_local_url_for_ap_post(url)


def search_by_ap_url(url, fetcher, max_retries=15) -> str | None:
    from common.validators import is_valid_url
    from takahe.models import InboxMessage

    if not is_valid_url(url):
        return

    # check if already known locally
    u = _get_local_url(url)
    if u:
        return u

    # send to takahe for signed fetch and processing
    m = {"type": "searchurl", "url": url}
    if fetcher:
        m["handle"] = fetcher.handle
    InboxMessage.create_internal(m)
    logger.debug(f"Sent searchurl message for {url}")

    # poll for result
    for i in range(max_retries):
        time.sleep(1)
        u = _get_local_url(url)
        if u:
            return u
    logger.debug(f"Waiting for {url} timeout")
