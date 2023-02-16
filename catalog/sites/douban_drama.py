from catalog.common import *
from catalog.models import *
from .douban import DoubanDownloader
import logging


_logger = logging.getLogger(__name__)


@SiteManager.register
class DoubanDrama(AbstractSite):
    SITE_NAME = SiteName.Douban
    ID_TYPE = IdType.DoubanDrama
    URL_PATTERNS = [r"\w+://www.douban.com/location/drama/(\d+)/"]
    WIKI_PROPERTY_ID = "P6443"
    DEFAULT_MODEL = Performance

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.douban.com/location/drama/" + id_value + "/"

    def scrape(self):
        h = DoubanDownloader(self.url).download().html()
        data = {}

        title_elem = h.xpath("/html/body//h1/span/text()")
        if title_elem:
            data["title"] = title_elem[0].strip()
        else:
            raise ParseError(self, "title")

        data["other_title"] = [s.strip() for s in title_elem[1:]]
        other_title_elem = h.xpath(
            "//dl//dt[text()='又名：']/following::dd[@itemprop='name']/text()"
        )
        data["other_title"] += other_title_elem
        data["other_title"] = list(set(data["other_title"]))

        plot_elem = h.xpath("//div[@id='link-report']/text()")
        if len(plot_elem) == 0:
            plot_elem = h.xpath("//div[@class='abstract']/text()")
        data["brief"] = "\n".join(plot_elem) if len(plot_elem) > 0 else ""

        data["genre"] = [
            s.strip()
            for s in h.xpath(
                "//dl//dt[text()='类型：']/following-sibling::dd[@itemprop='genre']/text()"
            )
        ]
        data["version"] = [
            s.strip()
            for s in h.xpath(
                "//dl//dt[text()='版本：']/following-sibling::dd[@class='titles']/a//text()"
            )
        ]
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
        data["actor"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='主演：']/following-sibling::dd/a[@itemprop='actor']//text()"
            )
        ]

        date_elem = h.xpath("//dl//dt[text()='演出日期：']/following::dd/text()")
        data["opening_date"] = date_elem[0] if date_elem else None

        data["theatre"] = [
            s.strip()
            for s in h.xpath(
                "//div[@class='meta']/dl//dt[text()='演出剧院：']/following-sibling::dd/a[@itemprop='location']//text()"
            )
        ]

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
