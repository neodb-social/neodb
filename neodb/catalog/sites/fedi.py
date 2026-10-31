import logging
import re
from urllib.parse import quote_plus, urlparse

import httpx
from django.conf import settings
from django.core.validators import URLValidator

from catalog.common import (
    AbstractSite,
    BasicImageDownloader,
    CachedDownloader,
    IdType,
    ResourceContent,
    SiteManager,
    SiteName,
)
from catalog.common.downloaders import DownloadError, short_requests
from catalog.common.scrapers import ParseError
from catalog.models import (
    Album,
    Edition,
    ExternalResource,
    Game,
    Item,
    ItemCategory,
    Movie,
    Performance,
    PerformanceProduction,
    Podcast,
    TVEpisode,
    TVSeason,
    TVShow,
)
from catalog.search import ExternalSearchResultItem, record_search_failure
from common.models import SiteConfig
from common.sentry import count as sentry_count

logger = logging.getLogger(__name__)


def _is_live(item: Item) -> bool:
    return not item.is_deleted and item.merged_to_item_id is None


@SiteManager.register
class FediverseInstance(AbstractSite):
    SITE_NAME = SiteName.Fediverse
    ID_TYPE = IdType.Fediverse
    URL_PATTERNS = []
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = None
    id_type_mapping = {
        "isbn": IdType.ISBN,
        "imdb": IdType.IMDB,
        "barcode": IdType.GTIN,
    }
    supported_types = {
        "book": Edition,
        "edition": Edition,
        "movie": Movie,
        "tvshow": TVShow,
        "tvseason": TVSeason,
        "tvepisode": TVEpisode,
        "album": Album,
        "game": Game,
        "podcast": Podcast,
        "performance": Performance,
        "performanceproduction": PerformanceProduction,
    }
    request_header = {
        "User-Agent": settings.NEODB_USER_AGENT,
        "Accept": "application/activity+json",
    }
    # linked resources fetched before a new peer item is created, and the
    # per-request timeout for each of them
    LINKED_FETCH_LIMIT = 3
    LINKED_FETCH_TIMEOUT = 5

    def __init__(self, url=None, id_value=None):
        super().__init__(url, id_value)
        self.linked_sites: list[AbstractSite] = []
        self.linked_item: Item | None = None

    @classmethod
    def id_to_url(cls, id_value):
        return id_value

    @classmethod
    def url_to_id(cls, url: str):
        u = url.split("://", 1)[1].split("?", 1)[0].split("/", 1)
        return "https://" + u[0].lower() + "/" + re.sub("^api/", "", u[1])
        # return "https://" + u[0].lower() + "/" + u[1]
        # FIXME re.sub(...) should be removed after all peers in network upgrade to 0.11.4.9+

    @classmethod
    def validate_url_fallback(cls, url: str):
        from takahe.utils import Takahe

        val = URLValidator()
        host = None
        try:
            val(url)
            u = cls.url_to_id(url)
            host = u.split("://", 1)[1].split("/", 1)[0].lower()
            if host in settings.SITE_DOMAINS:
                # disallow local instance URLs
                return False
            if host in Takahe.get_blocked_peers():
                return False
            return cls.get_json_from_url(u) is not None
        except DownloadError:
            if host and host in Takahe.get_neodb_peers():
                logger.warning(f"Fedi item url download error: {url}")
            return False
        except Exception as e:
            if host and host in Takahe.get_neodb_peers():
                logger.error(f"Fedi item url validation error: {url} {e}")
            return False

    @classmethod
    def is_local_item_url(cls, url: str) -> bool:
        """Check if the given URL belongs to a local item"""
        parsed = urlparse(url)
        if not parsed.hostname:
            return False
        # hostname drops any :port and userinfo; netloc keeps the port, which
        # SITE_DOMAINS may carry in local setups
        return (
            parsed.hostname in settings.SITE_DOMAINS
            or parsed.netloc.lower() in settings.SITE_DOMAINS
        )

    def get_local_item_from_external_resources(self) -> Item | None:
        """if a local item is in the external_resources, return it"""
        try:
            data = self.get_json_from_url(self.url)
        except DownloadError, ValueError:
            return None
        if not isinstance(data, dict):
            return None
        for ext in data.get("external_resources", []):
            u = ext.get("url")
            if u and self.is_local_item_url(u):
                i = Item.get_by_url(u, True)
                if i and not i.is_deleted:
                    return i

    @classmethod
    def get_json_from_url(cls, url):
        j = (
            CachedDownloader(url, headers=cls.request_header, timeout=2)
            .download()
            .json()
        )
        if (
            not isinstance(j, dict)
            or j.get("type", "").lower() not in cls.supported_types.keys()
        ):
            raise ValueError("Not a supported format or type")
        if j.get("id") != url:
            raise ValueError(f"ID mismatch: {j.get('id')} != {url}")
        return j

    def content_from_json(
        self, data: dict, detect_redirection: bool = True
    ) -> ResourceContent:
        """Build a ResourceContent from an item payload already in hand.

        Split out of scrape() so a caller holding the payload -- the
        catalog.ndjson of a backup -- can build the item without the origin
        server, which may be gone. Such a caller turns ``detect_redirection``
        off: its links already failed, so each HEAD only adds a timeout.
        """
        img_url = data.get("cover_image_url")
        raw_img, img_ext = (
            BasicImageDownloader.download_image(img_url, None, headers={})
            if img_url
            else (None, None)
        )
        ids = {}
        self.linked_sites = []
        data["preferred_model"] = data.get("type", "")
        data["prematched_resources"] = []
        model_cls = self.supported_types.get(data["preferred_model"].lower())
        if not model_cls:
            raise ParseError(self, "preferred_model")
        # `or []`: external_resources is nullable in the item schema
        for ext in data.get("external_resources") or []:
            u = ext.get("url") if isinstance(ext, dict) else None
            if not u or self.is_local_item_url(u):
                continue
            site = SiteManager.get_site_by_url(u, detect_redirection=detect_redirection)
            if not site:
                logger.error(f"FediverseInstance: {self.url} unsupported url {u}")
                continue
            if site.ID_TYPE == IdType.Fediverse:
                # TODO add support to link across instances
                continue
            if not site.check_model_compatibility(model_cls):
                logger.error(f"FediverseInstance: {self.url} incompatible url {u}")
                continue
            ids[site.ID_TYPE] = site.id_value
            self.linked_sites.append(site)
        for k, v in self.id_type_mapping.items():
            d = data.get(k)
            if d:
                ids[v] = d
        d = ResourceContent(
            metadata=data,
            cover_image=raw_img,
            cover_image_extention=img_ext,
            lookup_ids=ids,
        )
        return d

    def scrape(self):
        data = self.get_json_from_url(self.url)
        content = self.content_from_json(data)
        if self.get_resource().item_id is None:
            self.linked_item = self.resolve_linked_resources()
        return content

    def get_item(self, ignore_existing_content: bool = False, allow_rematch=True):
        p = self.get_resource()
        if self.linked_item and allow_rematch and p and p.ready:
            # the item a linked resource landed on beats a match on the
            # peer's own isbn / imdb / barcode, which may map elsewhere
            p.match_and_link_item(
                self.DEFAULT_MODEL,
                ignore_existing_content,
                preferred_item=self.linked_item,
            )
            return p.item
        return super().get_item(ignore_existing_content, allow_rematch)

    def resolve_linked_resources(self) -> Item | None:
        """Make the non-peer resources this peer item links to local, before
        this resource is matched for the first time.

        A linked resource is matched, or creates its item, on its own ids; the
        first such item is returned, and get_item() links this resource to it
        so no peer-only copy is created. Ids learned
        from the linked fetch are not copied to this resource: the peer's
        mapping can be wrong, and those ids would later pull other resources
        onto this item. If no linked resource can be made local, matching goes
        on as before and may create the copy; that fallback is counted so the
        dedup report can find it.
        """
        for site in self.linked_sites[: self.LINKED_FETCH_LIMIT]:
            existing = (
                ExternalResource.objects.filter(
                    id_type=site.ID_TYPE, id_value=site.id_value
                )
                .select_related("item")
                .first()
            )
            if existing and existing.item and _is_live(existing.item):
                return existing.item
            reason = "no_item"
            try:
                with short_requests(self.LINKED_FETCH_TIMEOUT):
                    res = site.get_resource_ready(auto_link=False)
                if res and res.item and _is_live(res.item):
                    return res.item
            except DownloadError:
                reason = "download"
            except Exception:
                logger.exception(f"FediverseInstance: {self.url} linked {site} error")
                reason = "error"
            logger.warning(
                f"FediverseInstance: {self.url} unable to resolve linked {site}",
                extra={"reason": reason},
            )
            sentry_count(
                "catalog.fedi.linked_fallback",
                attributes={"site": str(site.ID_TYPE), "reason": reason},
            )
        return None

    @classmethod
    async def peer_search_task(cls, host, q, page, category=None, page_size=5):
        p = (page - 1) * page_size // 20 + 1
        offset = (page - 1) * page_size % 20
        api_url = f"https://{host}/api/catalog/search?query={quote_plus(q)}&page={p}{'&category=' + category if category and category != 'all' else ''}"
        async with httpx.AsyncClient() as client:
            results = []
            try:
                response = await client.get(
                    api_url,
                    timeout=2,
                )
                r = response.json()
            except Exception as e:
                logger.warning(
                    f"Fediverse search {host} error",
                    extra={"url": api_url, "query": q, "exception": e},
                )
                reason = "timeout" if isinstance(e, httpx.TimeoutException) else "error"
                record_search_failure("fediverse", reason)
                return []
            if "data" in r:
                for item in r["data"]:
                    if any(
                        urlparse(res["url"]).hostname in settings.SITE_DOMAINS
                        for res in item.get("external_resources", [])
                    ):
                        continue
                    url = f"https://{host}{item['url']}"  # FIXME update API and use abs urls
                    try:
                        cat = ItemCategory(item["category"])
                    except Exception:
                        cat = None
                    results.append(
                        ExternalSearchResultItem(
                            cat,
                            host,
                            url,
                            item["display_title"],
                            "",
                            item["brief"],
                            item["cover_image_url"],
                        )
                    )
        return results[offset : offset + page_size]

    @classmethod
    def get_peers_for_search(cls) -> list[str]:
        from takahe.utils import Takahe

        if SiteConfig.system.search_peers:  # '-' = disable federated search
            return (
                []
                if SiteConfig.system.search_peers == ["-"]
                else SiteConfig.system.search_peers
            )
        return Takahe.get_neodb_peers()

    @classmethod
    def search_tasks(
        cls, q: str, page: int = 1, category: str | None = None, page_size=5
    ):
        peers = cls.get_peers_for_search()
        c = category if category != "movietv" else "movie,tv"
        return [cls.peer_search_task(host, q, page, c, page_size) for host in peers]
