import datetime
from typing import Dict, List, Literal, Optional

from django.conf import settings
from django.utils.dateparse import parse_datetime
from loguru import logger

from catalog.common.sites import SiteManager
from catalog.models import Edition, IdType, Item, SiteName
from journal.models import ShelfType
from users.models import Task

_PREFERRED_SITES = [
    SiteName.Fediverse,
    SiteName.RSS,
    SiteName.TMDB,
    SiteName.IMDB,
    SiteName.GoogleBooks,
    SiteName.Goodreads,
    SiteName.IGDB,
]


class BaseImporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    ImportResult = Literal["imported", "skipped", "failed"]
    TaskQueue = "import"
    DefaultMetadata = {
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "imported": 0,
        "failed": 0,
        "failed_items": [],
        "file": None,
        "visibility": 0,
    }

    def progress(self, result: ImportResult) -> None:
        """Update import progress.

        Args:
            result: The import result ('imported', 'skipped', or 'failed')
        """
        self.metadata["processed"] += 1
        self.metadata[result] = self.metadata.get(result, 0) + 1

        if self.metadata["total"]:
            progress_percentage = round(
                self.metadata["processed"] / self.metadata["total"] * 100
            )
            self.message = f"Progress: {progress_percentage}% - "
        else:
            self.message = ""
        self.message += (
            f"{self.metadata['imported']} imported, "
            f"{self.metadata['skipped']} skipped, "
            f"{self.metadata['failed']} failed"
        )
        self.save(update_fields=["metadata", "message"])

    def run(self) -> None:
        raise NotImplementedError

    def get_item_by_info_and_links(
        self, title: str, info_str: str, links: list[str]
    ) -> Optional[Item]:
        """Find an item based on information from CSV export.

        Args:
            title: Item title
            info_str: Item info string (space-separated key:value pairs)
            links_str: Space-separated URLs

        Returns:
            Item if found, None otherwise
        """
        site_url = settings.SITE_INFO["site_url"] + "/"
        # look for local items first
        for link in links:
            if link.startswith("/") or link.startswith(site_url):
                item = Item.get_by_url(link, resolve_merge=True)
                if item and not item.is_deleted:
                    return item

        sites = [
            SiteManager.get_site_by_url(link, detect_redirection=False)
            for link in links
        ]
        sites = [site for site in sites if site]
        sites.sort(
            key=lambda x: _PREFERRED_SITES.index(x.SITE_NAME)
            if x.SITE_NAME in _PREFERRED_SITES
            else 99
        )

        # match items without extra requests
        for site in sites:
            item = site.get_item()
            if item:
                return item

        # match items after HEAD
        sites = [
            SiteManager.get_site_by_url(site.url) if site.url else site
            for site in sites
        ]
        sites = [site for site in sites if site]
        for site in sites:
            item = site.get_item()
            if item:
                return item

        # fetch from remote
        for site in sites:
            try:
                logger.debug(f"fetching {site.url}")
                site.get_resource_ready()
                item = site.get_item()
                if item:
                    return item
            except Exception as e:
                logger.error(f"Error fetching item: {e}")

        # Try using the info string
        if info_str:
            info_dict = {}
            for pair in info_str.strip().split():
                if ":" in pair:
                    key, value = pair.split(":", 1)
                    info_dict[key] = value

            # Check for ISBN, IMDB, etc.
            item = None
            for key, value in info_dict.items():
                if key == "isbn" and value:
                    item = Edition.objects.filter(
                        primary_lookup_id_type=IdType.ISBN,
                        primary_lookup_id_value=value,
                    ).first()
                elif key == "imdb" and value:
                    item = Item.objects.filter(
                        primary_lookup_id_type=IdType.IMDB,
                        primary_lookup_id_value=value,
                    ).first()
                if item:
                    return item
        return None

    def parse_tags(self, tags_str: str) -> List[str]:
        """Parse pipe-separated tags string into a list of tags."""
        if not tags_str:
            return []
        return [tag.strip() for tag in tags_str.split("|") if tag.strip()]

    def parse_info(self, info_str: str) -> Dict[str, str]:
        """Parse info string into a dictionary."""
        info_dict = {}
        if not info_str:
            return info_dict

        for pair in info_str.split():
            if ":" in pair:
                key, value = pair.split(":", 1)
                info_dict[key] = value

        return info_dict

    def parse_datetime(self, timestamp_str: str | None) -> Optional[datetime.datetime]:
        """Parse ISO format timestamp into datetime."""
        if not timestamp_str:
            return None

        try:
            dt = parse_datetime(timestamp_str)
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.UTC)
            return dt
        except Exception as e:
            logger.error(f"Error parsing datetime {timestamp_str}: {e}")
            return None

    def parse_shelf_type(self, status_str: str) -> ShelfType:
        """Parse shelf type string into ShelfType enum."""
        if not status_str:
            return ShelfType.WISHLIST

        status_map = {
            "wishlist": ShelfType.WISHLIST,
            "progress": ShelfType.PROGRESS,
            "complete": ShelfType.COMPLETE,
            "dropped": ShelfType.DROPPED,
        }

        return status_map.get(status_str.lower(), ShelfType.WISHLIST)
