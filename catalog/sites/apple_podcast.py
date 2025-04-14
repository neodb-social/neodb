from urllib.parse import quote_plus

import httpx
from loguru import logger

from catalog.common import *
from catalog.models import *

from .rss import RSS


@SiteManager.register
class ApplePodcast(AbstractSite):
    SITE_NAME = SiteName.ApplePodcast
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

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category != "podcast":
            return []
        results = []
        search_url = f"https://itunes.apple.com/search?entity=podcast&limit={page * page_size}&term={quote_plus(q)}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(search_url, timeout=2)
                r = response.json()
                for p in r["results"][(page - 1) * page_size :]:
                    if p.get("feedUrl"):
                        results.append(
                            ExternalSearchResultItem(
                                ItemCategory.Podcast,
                                SiteName.RSS,
                                p["feedUrl"],
                                p["trackName"],
                                p["artistName"],
                                "",
                                p["artworkUrl600"],
                            )
                        )
            except httpx.ReadTimeout:
                logger.warning("ApplePodcast search timeout", extra={"query": q})
            except Exception as e:
                logger.error(
                    "ApplePodcast search error", extra={"query": q, "exception": e}
                )
        return results
