import asyncio

from django.core.cache import cache

from catalog.common import SiteManager
from catalog.common.models import ItemCategory
from catalog.search.models import ExternalSearchResultItem
from catalog.sites.fedi import FediverseInstance


class ExternalSources:
    @classmethod
    def search(
        cls,
        query: str,
        page: int = 1,
        category: str | None = None,
        visible_categories: list[ItemCategory] = [],
    ) -> list[ExternalSearchResultItem]:
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
