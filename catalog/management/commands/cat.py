from django.core.management.base import BaseCommand
import pprint
from catalog.common import SiteManager
from catalog.sites import *


class Command(BaseCommand):
    help = "Scrape a catalog item from external resource (and save it)"

    def add_arguments(self, parser):
        parser.add_argument("url", type=str, help="URL to scrape")
        parser.add_argument(
            "--save",
            action="store_true",
            help="save to database",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="force redownload",
        )

    def handle(self, *args, **options):
        url = str(options["url"])
        site = SiteManager.get_site_by_url(url)
        if site is None:
            self.stdout.write(self.style.ERROR(f"Unknown site for {url}"))
            return
        self.stdout.write(f"Fetching from {site}")
        if options["save"]:
            resource = site.get_resource_ready(ignore_existing_content=options["force"])
            pprint.pp(resource.metadata)
            pprint.pp(site.get_item())
            pprint.pp(site.get_item().metadata)
        else:
            resource = site.scrape()
            pprint.pp(resource.metadata)
            pprint.pp(resource.lookup_ids)
        self.stdout.write(self.style.SUCCESS(f"Done."))
