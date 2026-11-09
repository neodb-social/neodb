import logging

from catalog.common import SiteManager
from catalog.common.downloaders import DownloadError
from catalog.common.scrapers import ParseError
from catalog.models import Edition
from catalog.sites.bookwyrm import BookWyrm

logger = logging.getLogger(__name__)


def resolve_book(url: str, author_host: str | None = None) -> Edition | None:
    """The live Edition for a BookWyrm book URL, imported if needed.

    author_host is the host of the actor who referred to the book. When it is
    the book's own host, that actor's server serves the book, so the URL is
    taken even before takahe has seen the server's nodeinfo.
    """
    host = BookWyrm.book_host(url)
    if not host or BookWyrm.host_refused(host):
        return None
    if host == author_host:
        site = BookWyrm(url)
    else:
        site = SiteManager.get_site_by_url(url, detect_redirection=False)
        if not isinstance(site, BookWyrm):
            return None
    try:
        site.get_resource_ready()
        item = site.get_item()
    except (DownloadError, ParseError, ValueError) as e:
        logger.warning(f"BookWyrm book not fetched: {site.url}: {e}")
        return None
    if (
        isinstance(item, Edition)
        and not item.is_deleted
        and item.merged_to_item_id is None
    ):
        return item
    return None
