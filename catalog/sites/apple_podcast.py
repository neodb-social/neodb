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
        feed_url = r["feedUrl"]
        title = r["trackName"]
        pd = ResourceContent(
            metadata={
                "title": title,
                "feed_url": feed_url,
                "host": [r["artistName"]],
                "genres": r["genres"],
                "cover_image_url": r["artworkUrl600"],
            }
        )
        pd.lookup_ids[IdType.RSS] = RSS.url_to_id(feed_url)
        return pd
