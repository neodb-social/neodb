import asyncio
import logging

from catalog.common import SiteManager
from catalog.search.models import ExternalSearchResultItem
from catalog.sites.fedi import FediverseInstance

SEARCH_PAGE_SIZE = 5  # not all apis support page size
logger = logging.getLogger(__name__)


class ExternalSources:
    @classmethod
    def search(
        cls, query: str, page: int = 1, category: str | None = None
    ) -> list[ExternalSearchResultItem]:
        if not query or page < 1 or page > 10:
            return []
        if category in ["", None]:
            category = "all"
        tasks = FediverseInstance.search_tasks(query, page, category)
        for site in SiteManager.get_sites_for_search():
            tasks.append(site.search_task(query, page, category))
        # loop = asyncio.get_event_loop()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = []
        for r in loop.run_until_complete(asyncio.gather(*tasks)):
            results.extend(r)
        return results
