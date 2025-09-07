import asyncio
from urllib.parse import quote_plus

from django.core.cache import cache

from catalog.models import ItemCategory, SiteName


class ExternalSearchResultItem:
    def __init__(
        self,
        category: ItemCategory | None,
        source_site: SiteName,
        source_url: str,
        title: str,
        subtitle: str,
        brief: str,
        cover_url: str,
    ):
        self.class_name = "base"
        self.category = category
        self.external_resources = {
            "all": [
                {
                    "url": source_url,
                    "site_name": source_site,
                    "site_label": source_site,
                }
            ]
        }
        self.source_site = source_site
        self.source_url = source_url
        self.display_title = title
        self.subtitle = subtitle
        self.display_description = brief
        self.cover_image_url = cover_url

    def __repr__(self):
        return f"[{self.category}] {self.display_title} {self.source_url}"

    @property
    def verbose_category_name(self):
        return self.category.label if self.category else ""

    @property
    def url(self):
        return f"/search?q={quote_plus(self.source_url)}"

    @property
    def scraped(self):
        return False


class ExternalSources:
    @classmethod
    def search(
        cls,
        query: str,
        page: int = 1,
        category: str | None = None,
        visible_categories: list[ItemCategory] = [],
    ) -> list[ExternalSearchResultItem]:
        from catalog.common import SiteManager
        from catalog.sites import FediverseInstance

        if not query or page < 1 or page > 10 or not query or len(query) > 100:
            return []
        if category in ["", None]:
            category = "all"
        page_size = 5 if category == "all" else 10
        match category:
            case "all":
                cache_key = f"search_{','.join(visible_categories)}_{query}"
            case "movietv":
                cache_key = f"search_movie,tv_{query}"
            case _:
                cache_key = f"search_{category}_{query}"
        results = cache.get("ext_" + cache_key, None)
        if results is None:
            tasks = FediverseInstance.search_tasks(query, page, category, page_size)
            for site in SiteManager.get_sites_for_search():
                tasks.append(site.search_task(query, page, category, page_size))
            # loop = asyncio.get_event_loop()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = []
            for r in loop.run_until_complete(asyncio.gather(*tasks)):
                results.extend(r)
            cache.set("ext_" + cache_key, results, 300)
        dedupe_urls = cache.get(cache_key, [])
        results = [i for i in results if i.source_url not in dedupe_urls]
        return results
