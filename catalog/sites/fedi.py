import re

from django.core.validators import URLValidator
from loguru import logger

from catalog.common import *
from catalog.models import *


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
        "Book": Edition,
        "Movie": Movie,
        "TVShow": TVShow,
        "TVSeason": TVSeason,
        "TVEpisode": TVEpisode,
        "Album": Album,
        "Game": Game,
        "Podcast": Podcast,
        "Performance": Performance,
        "PerformanceProduction": PerformanceProduction,
    }
    request_header = {"User-Agent": "NeoDB/0.5", "Accept": "application/activity+json"}

    @classmethod
    def id_to_url(cls, id_value):
        return id_value

    @classmethod
    def url_to_id(cls, url: str):
        u = url.split("://", 1)[1].split("/", 1)
        return "https://" + u[0].lower() + "/" + u[1]

    @classmethod
    def validate_url_fallback(cls, url):
        val = URLValidator()
        try:
            val(url)
            if (
                url.split("://", 1)[1].split("/", 1)[0].lower()
                == settings.SITE_INFO["site_domain"]
            ):
                # disallow local instance URLs
                return False
            return cls.get_json_from_url(url) is not None
        except Exception:
            return False

    @classmethod
    def get_json_from_url(cls, url):
        j = CachedDownloader(url, headers=cls.request_header).download().json()
        if j.get("type") not in cls.supported_types.keys():
            raise ValueError("Not a supported format or type")
        if j.get("id") != url:
            logger.warning(f"ID mismatch: {j.get('id')} != {url}")
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
        data["preferred_model"] = data.get("type")
        data["prematched_resources"] = []
        for ext in data.get("external_resources", []):
            site = SiteManager.get_site_by_url(ext.get("url"))
            if site and site.ID_TYPE != self.ID_TYPE:
                ids[site.ID_TYPE] = site.id_value
                data["prematched_resources"].append(
                    {
                        "model": data["preferred_model"],
                        "id_type": site.ID_TYPE,
                        "id_value": site.id_value,
                        "url": site.url,
                    }
                )
        # for k, v in self.id_type_mapping.items():
        #     if data.get(k):
        #         ids[v] = data.get(k)
        d = ResourceContent(
            metadata=data,
            cover_image=raw_img,
            cover_image_extention=img_ext,
            lookup_ids=ids,
        )
        return d
