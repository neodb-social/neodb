import re
from urllib.parse import urljoin

from django.core.management.base import BaseCommand
from loguru import logger

from catalog.common import *


class Command(BaseCommand):
    help = "Crawl content"

    def add_arguments(self, parser):
        parser.add_argument("start", type=str, help="URL to start with")
        parser.add_argument("--pattern", help="pattern to navigate", action="store")

    def handle(self, *args, **options):
        logger.info("Crawl starts.")
        queue = [str(options["start"])]
        pattern = options["pattern"] or ""
        history = []
        item_patterns = []
        for site in SiteManager.registry.values():
            if site.URL_PATTERNS:
                item_patterns += site.URL_PATTERNS
        while queue and len(history) < 1000:
            url = queue.pop(0)
            history.append(url)
            logger.info(f"Navigating {url}")
            content = ProxiedDownloader(url).download().html()
            urls = content.xpath("//a/@href")
            for _u in urls:  # type:ignore
                u = urljoin(url, _u)
                if u not in history and u not in queue:
                    if len([p for p in item_patterns if re.match(p, u)]) > 0:
                        site = SiteManager.get_site_by_url(u)
                        if site:
                            u = site.url
                            if u not in history:
                                history.append(u)
                                logger.info(f"Fetching {u}")
                                site.get_resource_ready()
                        else:
                            logger.warning(f"unable to parse {u}")
                    elif pattern and u.find(pattern) >= 0:
                        queue.append(u)
        logger.info("Crawl finished.")
