from django.core.management.base import BaseCommand
from catalog.common import *
import re
from urllib.parse import urljoin


class Command(BaseCommand):
    help = "Crawl content"

    def add_arguments(self, parser):
        parser.add_argument("start", type=str, help="URL to start with")
        parser.add_argument("--pattern", help="pattern to navigate", action="store")

    def handle(self, *args, **options):
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
            self.stdout.write(f"Navigating {url}")
            content = ProxiedDownloader(url).download().html()
            urls = content.xpath("//a/@href")
            for _u in urls:
                u = urljoin(url, _u)
                if u not in history and u not in queue:
                    if len([p for p in item_patterns if re.match(p, u)]) > 0:
                        site = SiteManager.get_site_by_url(u)
                        u = site.url
                        if u not in history:
                            history.append(u)
                            self.stdout.write(f"Fetching {u}")
                            site.get_resource_ready()
                    elif pattern and u.find(pattern) >= 0:
                        queue.append(u)
        self.stdout.write(self.style.SUCCESS(f"Done."))
