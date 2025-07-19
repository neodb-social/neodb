import logging

from django.conf import settings

from catalog.common import *
from catalog.models import *
from common.models.lang import SITE_PREFERRED_LANGUAGES
from journal.models.renderers import html_to_text

from .igdb import search_igdb_by_3p_url

_logger = logging.getLogger(__name__)


def _get_preferred_languages():
    langs = {}
    for la in SITE_PREFERRED_LANGUAGES:
        if la == "zh":
            langs.update({"zh-cn": "zh-CN", "zh-tw": "zh-TW"})
            # zh-HK data is not good
        else:
            langs[la] = la
    return langs


STEAM_PREFERRED_LANGS = _get_preferred_languages()


@SiteManager.register
class Steam(AbstractSite):
    SITE_NAME = SiteName.Steam
    ID_TYPE = IdType.Steam
    URL_PATTERNS = [r"\w+://store\.steampowered\.com/app/(\d+)"]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(cls, id_value):
        return "https://store.steampowered.com/app/" + str(id_value)

    def download(self, lang):
        api_url = (
            f"https://store.steampowered.com/api/appdetails?appids={self.id_value}"
        )
        if settings.STEAM_API_KEY:
            api_url += f"&key={settings.STEAM_API_KEY}"
        headers = {
            "User-Agent": settings.NEODB_USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": STEAM_PREFERRED_LANGS[lang],
        }
        return BasicDownloader(api_url, headers=headers).download().json()

    def scrape(self):
        i = search_igdb_by_3p_url(self.url)
        pd = i.scrape() if i else ResourceContent()

        en_data = {}
        localized_title = []
        localized_desc = []
        for lang in STEAM_PREFERRED_LANGS.keys():
            data = self.download(lang).get(self.id_value, {}).get("data", {})
            name = data.get("name")
            if not name:
                continue
            if lang == "en":
                en_data = data
            localized_title.append({"lang": lang, "text": name})
            desc = html_to_text(data["detailed_description"])
            localized_desc.append({"lang": lang, "text": desc})
        if not en_data:
            en_data = self.download("en")
        if not en_data or not en_data.get("name"):
            raise ParseError(self, "id")
        # merge data from IGDB, use localized Steam data if available
        d = {
            "developer": en_data.get("developers", []),
            "publisher": en_data.get("publishers", []),
            "release_date": en_data.get("release_date", {}).get("date"),
            "genre": [g["description"] for g in en_data.get("genres", [])],
            "platform": ["PC"],
        }
        if en_data["release_date"].get("date"):
            d["release_date"] = en_data["release_date"].get("date")
        d.update(pd.metadata)
        d.update(
            {
                "localized_title": localized_title,
                "localized_description": localized_desc,
            }
        )
        pd.metadata = d

        # try Steam images if no image from IGDB
        header = en_data.get("header_image")
        if header:
            if pd.cover_image is None:
                cover = header.replace("header.jpg", "library_600x900_2x.jpg")
                pd.metadata["cover_image_url"] = cover
                (
                    pd.cover_image,
                    pd.cover_image_extention,
                ) = BasicImageDownloader.download_image(
                    pd.metadata["cover_image_url"], self.url
                )
            if pd.cover_image is None:
                pd.metadata["cover_image_url"] = header
                (
                    pd.cover_image,
                    pd.cover_image_extention,
                ) = BasicImageDownloader.download_image(
                    pd.metadata["cover_image_url"], self.url
                )
        return pd
