import json
import logging
import re
import urllib.parse

import dateparser
import dns.resolver

from catalog.common import *
from catalog.models import *

_logger = logging.getLogger(__name__)


@SiteManager.register
class Bandcamp(AbstractSite):
    SITE_NAME = SiteName.Bandcamp
    ID_TYPE = IdType.Bandcamp
    URL_PATTERNS = [r"https://([a-z0-9\-]+.bandcamp.com/album/[^?#/]+).*"]
    URL_PATTERN_FALLBACK = r"https://([a-z0-9\-\.]+/album/[^?#/]+).*"
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://{id_value}"

    @classmethod
    def validate_url_fallback(cls, url):
        if re.match(cls.URL_PATTERN_FALLBACK, url) is None:
            return False
        parsed_url = urllib.parse.urlparse(url)
        hostname = parsed_url.netloc
        try:
            answers = dns.resolver.query(hostname, "CNAME")
            for rdata in answers:  # type:ignore
                if str(rdata.target) == "dom.bandcamp.com.":
                    return True
        except Exception:
            pass
        try:
            answers = dns.resolver.query(hostname, "A")
            for rdata in answers:  # type:ignore
                if str(rdata.address) == "35.241.62.186":
                    return True
        except Exception:
            pass
        return False

    def scrape(self):
        content = BasicDownloader(self.url).download().html()
        try:
            title = self.query_str(content, "//h2[@class='trackTitle']/text()")
            artist = [
                self.query_str(content, "//div[@id='name-section']/h3/span/a/text()")
            ]
        except IndexError:
            raise ValueError("given url contains no valid info")

        genre = []  # TODO: parse tags
        track_list = ""
        try:
            release_str = re.sub(
                r"releas\w+ ",
                "",
                self.query_str(
                    content, "//div[@class='tralbumData tralbum-credits']/text()"
                ),
            )
            release_datetime = dateparser.parse(release_str) if release_str else None
            release_date = (
                release_datetime.strftime("%Y-%m-%d") if release_datetime else None
            )
        except Exception:
            release_date = None
        duration = None
        company = None
        brief_nodes = content.xpath("//div[@class='tralbumData tralbum-about']/text()")
        brief = "".join(brief_nodes) if brief_nodes else None  # type:ignore
        cover_url = self.query_str(content, "//div[@id='tralbumArt']/a/@href")
        bandcamp_page_data = json.loads(
            self.query_str(content, "//meta[@name='bc-page-properties']/@content")
        )
        bandcamp_album_id = bandcamp_page_data["item_id"]

        data = {
            "title": title,
            "artist": artist,
            "genre": genre,
            "track_list": track_list,
            "release_date": release_date,
            "duration": duration,
            "company": company,
            "brief": brief,
            "bandcamp_album_id": bandcamp_album_id,
            "cover_image_url": cover_url,
        }
        pd = ResourceContent(metadata=data)
        if data["cover_image_url"]:
            imgdl = BasicImageDownloader(data["cover_image_url"], self.url)
            try:
                pd.cover_image = imgdl.download().content
                pd.cover_image_extention = imgdl.extention
            except Exception:
                _logger.debug(
                    f'failed to download cover for {self.url} from {data["cover_image_url"]}'
                )
        return pd
