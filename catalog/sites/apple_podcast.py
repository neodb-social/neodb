import logging

from catalog.common import *
from catalog.models import *

from .rss import RSS

_logger = logging.getLogger(__name__)


@SiteManager.register
class ApplePodcast(AbstractSite):
    # SITE_NAME = SiteName.ApplePodcast
    ID_TYPE = IdType.ApplePodcast
    URL_PATTERNS = [r"https://[^.]+.apple.com/\w+/podcast/*[^/?]*/id(\d+)"]
    WIKI_PROPERTY_ID = "P5842"
    DEFAULT_MODEL = Podcast

    @classmethod
    def id_to_url(cls, id_value):
        return "https://podcasts.apple.com/us/podcast/id" + id_value

    def scrape(self):
        api_url = f"https://itunes.apple.com/lookup?id={self.id_value}"
        dl = BasicDownloader(api_url)
        resp = dl.download()
        r = resp.json()["results"][0]
        pd = ResourceContent(
            metadata={
                "title": r["trackName"],
                "feed_url": r["feedUrl"],
                "hosts": [r["artistName"]],
                "genres": r["genres"],
                "cover_image_url": r["artworkUrl600"],
            }
        )
        pd.lookup_ids[IdType.RSS] = RSS.url_to_id(pd.metadata.get("feed_url"))
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
