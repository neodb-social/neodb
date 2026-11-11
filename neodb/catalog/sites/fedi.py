import logging
import re
from urllib.parse import quote_plus, urljoin, urlparse

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
from common.validators import is_valid_url

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
            if host in Takahe.get_bookwyrm_peers():
                # BookWyrm editions pass the type and id checks below
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
        # the id of a fedi resource is its URL, so a site built from a stored
        # or linked id has not been through get_site_by_url's check
        if not is_valid_url(url):
            raise ValueError("Not a public URL")
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
        if "category" not in j:
            # every NeoDB item carries it; a BookWyrm Edition does not
            raise ValueError("Not a NeoDB item")
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
        # the payload becomes ExternalResource.metadata, where these keys tell
        # the site which further resources to fetch: never the peer's call
        for key in ("required_resources", "related_resources"):
            data.pop(key, None)
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
            existing = ExternalResource.objects.filter(
                id_type=site.ID_TYPE, id_value=site.id_value
            ).first()
            # the polymorphic manager loads the subclass; select_related would not
            item = (
                Item.objects.filter(pk=existing.item_id).first()
                if existing and existing.item_id
                else None
            )
            if item and _is_live(item):
                return item
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

    @staticmethod
    def _same_host(url: str, host: str) -> bool:
        """
        True when url is https on host or a subdomain of it, so that a peer's
        nodeinfo or search results can never point traffic at a third party.
        """
        try:
            u = urlparse(url)
            h = (u.hostname or "").lower()
            # a manually listed peer may carry a port
            host = (urlparse(f"https://{host}").hostname or "").lower()
        except ValueError:
            return False
        return (
            bool(host) and u.scheme == "https" and (h == host or h.endswith("." + host))
        )

    @classmethod
    def peer_search_endpoint(cls, host: str, advertised: str | None) -> str:
        default = f"https://{host}/api/catalog/search"
        if not advertised:
            return default
        try:
            u = urlparse(advertised)
        except ValueError:
            return default
        if (
            u.fragment
            or u.username
            or u.password
            or not cls._same_host(advertised, host)
        ):
            return default
        return advertised

    @classmethod
    def peer_search_result(
        cls, host: str, item: object
    ) -> ExternalSearchResultItem | None:
        """
        Peers may run older NeoDB or other software, so any field of an item
        may be missing or malformed; such an item is skipped, not fatal.
        """
        if not isinstance(item, dict):
            return None
        title = item.get("display_title")
        path = item.get("url")
        if not title or not isinstance(title, str) or not isinstance(path, str):
            return None
        try:
            # NeoDB returns a path; a full URL is accepted on the peer's own host
            url = urljoin(f"https://{host}/", path.strip())
            if not cls._same_host(url, host):
                return None
            for res in item.get("external_resources") or []:
                if (
                    isinstance(res, dict)
                    and urlparse(str(res.get("url") or "")).hostname
                    in settings.SITE_DOMAINS
                ):
                    return None
        except ValueError, TypeError:
            return None
        try:
            cat = ItemCategory(item.get("category"))
        except ValueError:
            cat = None
        brief = item.get("brief")
        cover = item.get("cover_image_url")
        return ExternalSearchResultItem(
            cat,
            host,
            url,
            title,
            "",
            brief if isinstance(brief, str) else "",
            cover if isinstance(cover, str) else "",
        )

    @classmethod
    async def peer_search_task(
        cls, host, q, page, category=None, page_size=5, endpoint: str | None = None
    ):
        # endpoint is resolved by the sync caller: the settings lookup may hit
        # the database, which a coroutine must not do
        p = (page - 1) * page_size // 20 + 1
        offset = (page - 1) * page_size % 20
        endpoint = cls.peer_search_endpoint(host, endpoint)
        api_url = f"{endpoint}{'&' if '?' in endpoint else '?'}query={quote_plus(q)}&page={p}{'&category=' + category if category and category != 'all' else ''}"
        async with httpx.AsyncClient() as client:
            results = []
            try:
                response = await client.get(
                    api_url,
                    headers={"User-Agent": settings.NEODB_USER_AGENT},
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
            data = r.get("data") if isinstance(r, dict) else None
            for item in data if isinstance(data, list) else []:
                result = cls.peer_search_result(host, item)
                if result:
                    results.append(result)
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
        from takahe.utils import Takahe

        peers = cls.get_peers_for_search()
        if not peers:
            return []
        endpoints = Takahe.get_neodb_search_endpoints()
        c = category if category != "movietv" else "movie,tv"
        return [
            cls.peer_search_task(host, q, page, c, page_size, endpoints[host])
            for host in peers
            if host in endpoints
        ]
