import json
import os
import tempfile
import zipfile

from django.utils.dateparse import parse_datetime
from loguru import logger

from catalog.common.sites import SiteManager
from catalog.models import IdType, Item
from catalog.models.tv import TVShow
from journal.models import Collection, Mark, ShelfType
from users.models import Task


class TraktImporter(Task):
    class Meta:
        app_label = "journal"

    TaskQueue = "import"
    DefaultMetadata = {
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "imported": 0,
        "failed": 0,
        "visibility": 0,
        "failed_items": [],
        "file": None,
    }

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        try:
            return zipfile.is_zipfile(uploaded_file)
        except Exception:
            return False

    def progress(self, mark_state: int, label: str | None = None) -> None:
        self.metadata["processed"] += 1
        match mark_state:
            case 1:
                self.metadata["imported"] += 1
            case 0:
                self.metadata["skipped"] += 1
            case _:
                self.metadata["failed"] += 1
                if label:
                    self.metadata["failed_items"].append(label)
        self.message = (
            f"{self.metadata['imported']} imported, "
            f"{self.metadata['skipped']} skipped, "
            f"{self.metadata['failed']} failed"
        )
        self.save(update_fields=["metadata", "message"])

    def _resolve_tv_season(self, item: Item, tmdb_id: int | None) -> Item:
        """If item is a TVShow, resolve to its first season."""
        if not isinstance(item, TVShow):
            return item
        # Check if season 1 already exists in DB
        season = item.seasons.filter(season_number=1).first()
        if season:
            return season
        # Fetch season 1 via TMDB
        if tmdb_id:
            try:
                url = f"https://www.themoviedb.org/tv/{tmdb_id}/season/1"
                site = SiteManager.get_site_by_url(url, detect_redirection=False)
                if site:
                    site.get_resource_ready()
                    season_item = site.get_item()
                    if season_item:
                        return season_item
            except Exception as e:
                logger.warning(f"Failed to fetch season 1 for TMDB {tmdb_id}: {e}")
        return item

    def _find_item(self, media_type: str, ids: dict) -> Item | None:
        """Resolve a Trakt media item to a NeoDB Item via IMDB or TMDB IDs."""
        imdb_id = ids.get("imdb")
        tmdb_id = ids.get("tmdb")

        # Try IMDB first (works for both movies and shows)
        if imdb_id:
            site = SiteManager.get_site_by_id(IdType.IMDB, imdb_id)
            if site:
                item = site.get_item()
                if item:
                    return self._resolve_tv_season(item, tmdb_id)

        # Try TMDB by ID
        if tmdb_id:
            id_type = IdType.TMDB_Movie if media_type == "movie" else IdType.TMDB_TV
            site = SiteManager.get_site_by_id(id_type, str(tmdb_id))
            if site:
                item = site.get_item()
                if item:
                    return self._resolve_tv_season(item, tmdb_id)

        # Fetch from TMDB via URL
        if tmdb_id:
            if media_type == "movie":
                url = f"https://www.themoviedb.org/movie/{tmdb_id}"
            else:
                url = f"https://www.themoviedb.org/tv/{tmdb_id}"
            try:
                site = SiteManager.get_site_by_url(url, detect_redirection=False)
                if site:
                    site.get_resource_ready()
                    item = site.get_item()
                    if item:
                        return self._resolve_tv_season(item, tmdb_id)
            except Exception as e:
                logger.warning(f"TMDB fetch failed for {url}: {e}")

        # Fallback: fetch from IMDB
        if imdb_id:
            url = f"https://www.imdb.com/title/{imdb_id}/"
            try:
                site = SiteManager.get_site_by_url(url, detect_redirection=False)
                if site:
                    site.get_resource_ready()
                    item = site.get_item()
                    if item:
                        return self._resolve_tv_season(item, tmdb_id)
            except Exception as e:
                logger.warning(f"IMDB fetch failed for {url}: {e}")

        return None

    @staticmethod
    def _parse_timestamp(ts: str | None):
        import datetime

        if not ts:
            return None
        try:
            dt = parse_datetime(ts)
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.UTC)
            return dt
        except Exception:
            return None

    @staticmethod
    def _item_label(entry: dict) -> str:
        """Build a human-readable label for failed item logging."""
        for key in ("movie", "show"):
            media = entry.get(key)
            if media:
                title = media.get("title", "?")
                year = media.get("year", "")
                return f"{title} ({year})" if year else title
        return "unknown"

    @staticmethod
    def _get_media(entry: dict) -> tuple[str, dict] | None:
        """Extract media type and media object from an entry."""
        media_type = entry.get("type")
        if media_type in ("movie", "show"):
            media = entry.get(media_type)
            if media:
                return media_type, media
            return None
        # watched files don't always have "type", infer from keys
        for key in ("movie", "show"):
            if key in entry:
                return key, entry[key]
        return None

    def _make_key(self, media_type: str, ids: dict) -> str:
        imdb_id = ids.get("imdb", "")
        tmdb_id = ids.get("tmdb", "")
        return f"{media_type}:{imdb_id or tmdb_id}"

    def _mark_item(
        self,
        item: Item,
        shelf_type: ShelfType,
        rating_grade: int | None,
        created_time,
    ) -> None:
        owner = self.user.identity
        mark = Mark(owner, item)

        # Skip downgrades
        is_downgrade = (
            mark.shelf_type == ShelfType.COMPLETE and shelf_type != ShelfType.COMPLETE
        ) or (
            mark.shelf_type in [ShelfType.PROGRESS, ShelfType.DROPPED]
            and shelf_type == ShelfType.WISHLIST
        )
        if is_downgrade:
            self.progress(0)
            return

        # Skip if already on the same shelf
        if mark.shelf_type == shelf_type:
            self.progress(0)
            return

        visibility = self.metadata["visibility"]
        mark.update(
            shelf_type,
            comment_text=None,
            rating_grade=rating_grade,
            tags=None,
            visibility=visibility,
            created_time=created_time,
        )
        self.progress(1)

    def _load_json_file(self, tmpdir: str, filename: str) -> list[dict]:
        """Load a single JSON file, returning a list of entries."""
        filepath = os.path.join(tmpdir, filename)
        if not os.path.isfile(filepath):
            return []
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.warning(f"Failed to parse {filepath}: {e}")
        return []

    def _process_ratings(self, tmpdir: str, seen: set[str]) -> None:
        """Process ratings-movies.json and ratings-shows.json. Trakt ratings are 1-10."""
        for suffix in ("movies", "shows"):
            for entry in self._load_json_file(tmpdir, f"ratings-{suffix}.json"):
                result = self._get_media(entry)
                if not result:
                    continue
                media_type, media = result
                ids = media.get("ids", {})
                key = self._make_key(media_type, ids)
                if key in seen:
                    continue
                seen.add(key)

                label = self._item_label(entry)
                item = self._find_item(media_type, ids)
                if not item:
                    logger.warning(f"Trakt import: could not find item for {label}")
                    self.progress(-1, label)
                    continue

                rating = entry.get("rating")
                rating_grade = int(rating) if rating else None
                dt = self._parse_timestamp(entry.get("rated_at"))
                self._mark_item(item, ShelfType.COMPLETE, rating_grade, dt)

    def _process_watched(self, tmpdir: str, seen: set[str]) -> None:
        """Process watched-movies.json and watched-shows.json."""
        for suffix in ("movies", "shows"):
            for entry in self._load_json_file(tmpdir, f"watched-{suffix}.json"):
                result = self._get_media(entry)
                if not result:
                    continue
                media_type, media = result
                ids = media.get("ids", {})
                key = self._make_key(media_type, ids)
                if key in seen:
                    continue
                seen.add(key)

                label = self._item_label(entry)
                item = self._find_item(media_type, ids)
                if not item:
                    logger.warning(f"Trakt import: could not find item for {label}")
                    self.progress(-1, label)
                    continue

                dt = self._parse_timestamp(entry.get("last_watched_at"))
                self._mark_item(item, ShelfType.COMPLETE, None, dt)

    def _process_watchlist(self, tmpdir: str, seen: set[str]) -> None:
        """Process lists-watchlist.json."""
        for entry in self._load_json_file(tmpdir, "lists-watchlist.json"):
            result = self._get_media(entry)
            if not result:
                continue
            media_type, media = result
            ids = media.get("ids", {})
            key = self._make_key(media_type, ids)
            if key in seen:
                continue
            seen.add(key)

            label = self._item_label(entry)
            item = self._find_item(media_type, ids)
            if not item:
                logger.warning(f"Trakt import: could not find item for {label}")
                self.progress(-1, label)
                continue

            dt = self._parse_timestamp(entry.get("listed_at"))
            self._mark_item(item, ShelfType.WISHLIST, None, dt)

    def _process_lists(self, tmpdir: str) -> None:
        """Process custom lists from lists-lists.json and lists-list-*.json."""
        lists_meta = self._load_json_file(tmpdir, "lists-lists.json")
        all_files = os.listdir(tmpdir)
        for list_meta in lists_meta:
            list_ids = list_meta.get("ids", {})
            trakt_id = list_ids.get("trakt")
            slug = list_ids.get("slug", "")
            name = list_meta.get("name", "")
            description = list_meta.get("description", "")
            if not trakt_id:
                continue

            # Find the corresponding list items file
            pattern = f"lists-list-{trakt_id}-"
            items_file = None
            for fn in all_files:
                if fn.startswith(pattern) and fn.endswith(".json"):
                    items_file = fn
                    break
            if not items_file:
                logger.warning(
                    f"Trakt import: no items file for list '{name}' ({trakt_id})"
                )
                continue

            list_entries = self._load_json_file(tmpdir, items_file)
            if not list_entries:
                continue

            list_title = name or slug or "Trakt List"
            # Deduplicate: skip if a collection with the same title already exists
            existing = Collection.objects.filter(
                owner=self.user.identity, title=list_title
            ).first()
            if existing:
                logger.info(
                    f"Trakt import: list '{list_title}' already exists, skipping"
                )
                continue

            visibility = self.metadata["visibility"]
            collection = Collection.objects.create(
                title=list_title,
                brief=description or "",
                owner=self.user.identity,
                visibility=visibility,
            )

            for entry in list_entries:
                result = self._get_media(entry)
                if not result:
                    continue
                media_type, media = result
                ids = media.get("ids", {})
                label = self._item_label(entry)
                item = self._find_item(media_type, ids)
                if item:
                    note = entry.get("notes") or ""
                    collection.append_item(item, note=note)
                    self.progress(1)
                else:
                    logger.warning(
                        f"Trakt import: could not find item for list entry {label}"
                    )
                    self.progress(-1, label)

    def run(self) -> None:
        filename = self.metadata["file"]
        with zipfile.ZipFile(filename, "r") as zipref:
            with tempfile.TemporaryDirectory() as tmpdir:
                for member in zipref.namelist():
                    member_path = os.path.realpath(os.path.join(tmpdir, member))
                    if not member_path.startswith(
                        os.path.realpath(tmpdir) + os.sep
                    ) and member_path != os.path.realpath(tmpdir):
                        raise ValueError(
                            f"Zip member {member} would extract outside target directory"
                        )
                zipref.extractall(tmpdir)

                seen: set[str] = set()
                # Ratings first (has rating grade, marks as complete)
                self._process_ratings(tmpdir, seen)
                # Watched next (marks as complete, skips already-rated items)
                self._process_watched(tmpdir, seen)
                # Watchlist last (marks as wishlist, skips already-watched)
                self._process_watchlist(tmpdir, seen)
                # Custom lists (imported as collections)
                self._process_lists(tmpdir)

        self.metadata["total"] = self.metadata["processed"]
        self.message = (
            f"{self.metadata['imported']} imported, "
            f"{self.metadata['skipped']} skipped, "
            f"{self.metadata['failed']} failed"
        )
        self.save(update_fields=["metadata", "message"])
