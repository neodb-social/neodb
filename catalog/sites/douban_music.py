import logging

import dateparser

from catalog.common import *
from catalog.models import *
from catalog.music.utils import upc_to_gtin_13

from .douban import DoubanDownloader

_logger = logging.getLogger(__name__)


@SiteManager.register
class DoubanMusic(AbstractSite):
    SITE_NAME = SiteName.Douban
    ID_TYPE = IdType.DoubanMusic
    URL_PATTERNS = [
        r"\w+://music\.douban\.com/subject/(\d+)/{0,1}",
        r"\w+://m.douban.com/music/subject/(\d+)/{0,1}",
        r"\w+://www.douban.com/doubanapp/dispatch\?uri=/music/(\d+)/",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return "https://music.douban.com/subject/" + id_value + "/"

    def scrape(self):
        content = DoubanDownloader(self.url).download().html()

        elem = content.xpath("//h1/span/text()")
        title = elem[0].strip() if len(elem) else None
        if not title:
            raise ParseError(self, "title")

        artists_elem = content.xpath(
            "//div[@id='info']/span/span[@class='pl']/a/text()"
        )
        artist = (
            None if not artists_elem else list(map(lambda a: a[:200], artists_elem))
        )

        genre_elem = content.xpath(
            "//div[@id='info']//span[text()='流派:']/following::text()[1]"
        )
        genre = genre_elem[0].strip().split(" / ") if genre_elem else []

        date_elem = content.xpath(
            "//div[@id='info']//span[text()='发行时间:']/following::text()[1]"
        )
        release_date = dateparser.parse(date_elem[0].strip()) if date_elem else None
        release_date = release_date.strftime("%Y-%m-%d") if release_date else None

        company_elem = content.xpath(
            "//div[@id='info']//span[text()='出版者:']/following::text()[1]"
        )
        company = company_elem[0].strip() if company_elem else None

        track_list_elem = content.xpath(
            "//div[@class='track-list']/div[@class='indent']/div/text()"
        )
        if track_list_elem:
            track_list = "\n".join([track.strip() for track in track_list_elem])
        else:
            track_list = None

        brief_elem = content.xpath("//span[@class='all hidden']")
        if not brief_elem:
            brief_elem = content.xpath("//span[@property='v:summary']")
        brief = (
            "\n".join([e.strip() for e in brief_elem[0].xpath("./text()")])
            if brief_elem
            else None
        )

        img_url_elem = content.xpath("//div[@id='mainpic']//img/@src")
        img_url = img_url_elem[0].strip() if img_url_elem else None

        data = {
            "title": title,
            "artist": artist,
            "genre": genre,
            "release_date": release_date,
            "duration": None,
            "company": [company] if company else [],
            "track_list": track_list,
            "brief": brief,
            "cover_image_url": img_url,
        }
        gtin = None
        isrc = None
        other_elem = content.xpath(
            "//div[@id='info']//span[text()='又名:']/following-sibling::text()[1]"
        )
        if other_elem:
            data["other_title"] = other_elem[0].strip().split(" / ")
        other_elem = content.xpath(
            "//div[@id='info']//span[text()='专辑类型:']/following-sibling::text()[1]"
        )
        if other_elem:
            data["album_type"] = other_elem[0].strip()
        other_elem = content.xpath(
            "//div[@id='info']//span[text()='介质:']/following-sibling::text()[1]"
        )
        if other_elem:
            data["media"] = other_elem[0].strip()
        other_elem = content.xpath(
            "//div[@id='info']//span[text()='ISRC:']/following-sibling::text()[1]"
        )
        if other_elem:
            isrc = other_elem[0].strip()
        other_elem = content.xpath(
            "//div[@id='info']//span[text()='条形码:']/following-sibling::text()[1]"
        )
        if other_elem:
            gtin = upc_to_gtin_13(other_elem[0].strip())
        other_elem = content.xpath(
            "//div[@id='info']//span[text()='碟片数:']/following-sibling::text()[1]"
        )
        if other_elem:
            data["disc_count"] = other_elem[0].strip()

        pd = ResourceContent(metadata=data)
        if gtin:
            pd.lookup_ids[IdType.GTIN] = gtin
        if isrc:
            pd.lookup_ids[IdType.ISRC] = isrc
        if pd.metadata["cover_image_url"]:
            imgdl = BasicImageDownloader(pd.metadata["cover_image_url"], self.url)
            try:
                pd.cover_image = imgdl.download().content
                pd.cover_image_extention = imgdl.extention
            except Exception:
                _logger.debug(
                    f'failed to download cover for {self.url} from {pd.metadata["cover_image_url"]}'
                )
        return pd
