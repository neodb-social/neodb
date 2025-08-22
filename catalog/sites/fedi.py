import re
from urllib.parse import quote_plus, urlparse

import httpx
from django.conf import settings
from django.core.validators import URLValidator
from loguru import logger

from catalog.common import (
    AbstractSite,
    BasicImageDownloader,
    CachedDownloader,
    IdType,
    ItemCategory,
    ResourceContent,
    SiteManager,
    SiteName,
)
from catalog.common.downloaders import DownloadError
from catalog.common.scrapers import ParseError
from catalog.models import (
    Album,
    Edition,
    ExternalSearchResultItem,
    Game,
    Movie,
    Performance,
    PerformanceProduction,
    Podcast,
    TVEpisode,
    TVSeason,
    TVShow,
)


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

    def scrape(self):
        data = self.get_json_from_url(self.url)
        img_url = data.get("cover_image_url")
        raw_img, img_ext = (
            BasicImageDownloader.download_image(img_url, None, headers={})
            if img_url
            else (None, None)
        )
        ids = {}
        data["preferred_model"] = data.get("type", "")
        data["prematched_resources"] = []
        model_cls = self.supported_types.get(data["preferred_model"].lower())
        if not model_cls:
            raise ParseError(self, "preferred_model")
        for ext in data.get("external_resources", []):
            u = ext.get("url")
            site = SiteManager.get_site_by_url(u)
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
            # data["prematched_resources"].append(
            #     {
            #         "model": data["preferred_model"],
            #         "id_type": site.ID_TYPE,
            #         "id_value": site.id_value,
            #         "url": site.url,
            #     }
            # )
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

        if settings.SEARCH_PEERS:  # '-' = disable federated search
            return [] if settings.SEARCH_PEERS == ["-"] else settings.SEARCH_PEERS
        return Takahe.get_neodb_peers()

    @classmethod
    def search_tasks(
        cls, q: str, page: int = 1, category: str | None = None, page_size=5
    ):
        peers = cls.get_peers_for_search()
        c = category if category != "movietv" else "movie,tv"
        return [cls.peer_search_task(host, q, page, c, page_size) for host in peers]
