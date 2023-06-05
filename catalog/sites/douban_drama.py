from catalog.common import *
from catalog.models import *
from .douban import DoubanDownloader
import logging
from lxml import html
from django.core.cache import cache
import re

_logger = logging.getLogger(__name__)


def _cache_key(url):
    return f"$:{url}"


@SiteManager.register
class DoubanDramaVersion(AbstractSite):
    """
    Parse Douban Drama Version section in Douban Drama page

    It's the same page as the drama page, each version resides in a <div id="1234" />
    since they all get parsed about the same time, page content will be cached to avoid duplicate fetch
    """

    SITE_NAME = SiteName.Douban
    ID_TYPE = IdType.DoubanDramaVersion
    URL_PATTERNS = [r"\w+://www.douban.com/location/drama/(\d+)/#(\d+)$"]

    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = PerformanceProduction

    @classmethod
    def url_to_id(cls, url: str):
        m = re.match(cls.URL_PATTERNS[0], url)
        if not m:
            return None
        return m.group(1) + "-" + m.group(2)

    @classmethod
    def id_to_url(cls, id_value):
        ids = id_value.split("-")
        return f"https://www.douban.com/location/drama/{ids[0]}/#{ids[1]}"

    def scrape(self):
        show_url = self.url.split("#")[0]
        show_id = self.id_value.split("-")[0]
        version_id = self.id_value.split("-")[1]

        key = _cache_key(show_url)
        r = cache.get(key, None)
        if r is None:
            r = DoubanDownloader(show_url).download().content.decode("utf-8")
            cache.set(key, r, 3600)
        h = html.fromstring(r)

        p = "//div[@id='" + version_id + "']"
        q = p + "//dt[text()='{}：']/following-sibling::dd[1]/a/span/text()"
        q2 = p + "//dt[text()='{}：']/following-sibling::dd[1]/text()"
        title = " ".join(h.xpath(p + "//h3/text()")).strip()
        if not title:
            raise ParseError(self, "title")
        data = {
            "title": title,
            "director": [x.strip() for x in h.xpath(q.format("导演"))],
            "playwright": [x.strip() for x in h.xpath(q.format("编剧"))],
            "performer": [x.strip() for x in h.xpath(q.format("主演"))],
            "composer": [x.strip() for x in h.xpath(q.format("作曲"))],
            "language": [x.strip() for x in h.xpath(q2.format("语言"))],
            "opening_date": " ".join(h.xpath(q2.format("演出日期"))).strip(),
            "troupe": [x.strip() for x in h.xpath(q.format("演出团体"))],
            "location": [x.strip() for x in h.xpath(q.format("演出剧院"))],
        }
        if data["opening_date"]:
            d = data["opening_date"].split("-")
            l = len(d) if len(d) < 6 else 6
            if l > 3:
                data["opening_date"] = "-".join(d[:3])
                data["closing_date"] = "-".join(d[0 : 6 - l] + d[3:l])
        img_url_elem = h.xpath("//img[@itemprop='image']/@src")
        data["cover_image_url"] = img_url_elem[0].strip() if img_url_elem else None
        pd = ResourceContent(metadata=data)
        pd.metadata["required_resources"] = [
            {
                "model": "Performance",
                "id_type": IdType.DoubanDrama,
                "id_value": show_id,
                "title": f"Douban Drama {show_id}",
                "url": show_url,
            }
        ]
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


@SiteManager.register
class DoubanDrama(AbstractSite):
    SITE_NAME = SiteName.Douban
    ID_TYPE = IdType.DoubanDrama
    URL_PATTERNS = [r"\w+://www.douban.com/location/drama/(\d+)/[^#]*$"]
    WIKI_PROPERTY_ID = "P6443"
    DEFAULT_MODEL = Performance

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.douban.com/location/drama/" + id_value + "/"

    def scrape(self):
        key = _cache_key(self.url)
        r = cache.get(key, None)
        if r is None:
            r = DoubanDownloader(self.url).download().content.decode("utf-8")
            cache.set(key, r, 3600)
        h = html.fromstring(r)
        data = {}

        title_elem = h.xpath("/html/body//h1/span/text()")
        if title_elem:
            data["title"] = title_elem[0].strip()
            data["orig_title"] = title_elem[1] if len(title_elem) > 1 else None
        else:
            raise ParseError(self, "title")

        other_title_elem = h.xpath(
            "//dl//dt[text()='又名：']/following::dd[@itemprop='name']/text()"
        )
        data["other_title"] = other_title_elem

        plot_elem = h.xpath("//div[@class='pure-text']/div[@class='full']/text()")
        if len(plot_elem) == 0:
            plot_elem = h.xpath(
                "//div[@class='pure-text']/div[@class='abstract']/text()"
            )
        if len(plot_elem) == 0:
            plot_elem = h.xpath("//div[@class='pure-text']/text()")
        data["brief"] = "\n".join(plot_elem)

        data["genre"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']//dl//dt[text()='类型：']/following-sibling::dd[@itemprop='genre']/text()"
            )
        ]
        # data["version"] = [
        #     s.strip()
        #     for s in h.xpath(
        #         "//dl//dt[text()='版本：']/following-sibling::dd[@class='titles']/a//text()"
        #     )
        # ]
        data["director"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='导演：']/following-sibling::dd/a[@itemprop='director']//text()"
            )
        ]
        data["composer"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='作曲：']/following-sibling::dd/a[@itemprop='musicBy']//text()"
            )
        ]
        data["choreographer"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='编舞：']/following-sibling::dd/a[@itemprop='choreographer']//text()"
            )
        ]
        data["troupe"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='演出团体：']/following-sibling::dd/a[@itemprop='performer']//text()"
            )
        ]
        data["playwright"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='编剧：']/following-sibling::dd/a[@itemprop='author']//text()"
            )
        ]
        data["performer"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='主演：']/following-sibling::dd/a[@itemprop='actor']//text()"
            )
        ]

        date_elem = h.xpath(
            "//div[@class='meta']//dl//dt[text()='演出日期：']/following::dd/text()"
        )
        data["opening_date"] = date_elem[0] if date_elem else None
        if data["opening_date"]:
            d = data["opening_date"].split("-")
            l = len(d) if len(d) < 6 else 6
            if l > 3:
                data["opening_date"] = "-".join(d[:3])
                data["closing_date"] = "-".join(d[0 : 6 - l] + d[3:l])

        data["location"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='演出剧院：']/following-sibling::dd/a[@itemprop='location']//text()"
            )
        ]

        versions = h.xpath("//div[@id='versions']/div[@class='fluid-mods']/div/@id")
        data["related_resources"] = list(
            map(
                lambda v: {
                    "model": "PerformanceProduction",
                    "id_type": IdType.DoubanDramaVersion,
                    "id_value": f"{self.id_value}-{v}",
                    "title": f"{data['title']} - {v}",
                    "url": f"{self.url}#{v}",
                },
                versions,
            )
        )
        img_url_elem = h.xpath("//img[@itemprop='image']/@src")
        data["cover_image_url"] = img_url_elem[0].strip() if img_url_elem else None

        pd = ResourceContent(metadata=data)
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
