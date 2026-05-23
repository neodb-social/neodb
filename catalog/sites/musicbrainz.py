"""
MusicBrainz

MusicBrainz is an open music encyclopedia that collects music metadata and makes it available to the public.
Using the MusicBrainz API to fetch album/release-group information.
"""

import logging
import re
import threading
from typing import Any, Dict, List

import httpx
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.common.rate_limit import RedisRateLimiter
from catalog.models import *
from catalog.search import ExternalSearchResultItem, record_search_failure
from common.models.lang import detect_language

_logger = logging.getLogger(__name__)


# MusicBrainz' documented guideline is 1 req/s/IP for the public API. The
# 50 req/s/IP cap only applies to negotiated high-volume clients; every other
# well-behaved client sharing our egress IP assumes the 1 req/s ceiling, so
# use that as our default.
_musicbrainz_limiter: RedisRateLimiter | None = None
_musicbrainz_limiter_lock = threading.Lock()


def musicbrainz_limiter() -> RedisRateLimiter:
    """Singleton limiter for musicbrainz.org calls."""
    global _musicbrainz_limiter
    # Double-checked locking so the hot path is a single None comparison and
    # concurrent first-callers can't end up with two RedisRateLimiter
    # instances (the underlying throttle would still work because the cursor
    # lives in Redis, but `is`-identity callers would see two objects).
    if _musicbrainz_limiter is None:
        with _musicbrainz_limiter_lock:
            if _musicbrainz_limiter is None:
                _musicbrainz_limiter = RedisRateLimiter(
                    key="ratelimit:musicbrainz.org",
                    rate=1.0,
                )
    return _musicbrainz_limiter


class MusicBrainzDownloader(BasicDownloader):
    """BasicDownloader that throttles every call through the shared Redis
    cursor so all NeoDB processes together honor MusicBrainz' 1 req/s/IP
    guideline. Use for any musicbrainz.org request; coverartarchive.org is a
    different host and stays on plain BasicDownloader.

    ``rate_limit_timeout`` lets batch callers (background fan-out jobs, RYM
    import) wait minutes rather than fall open like an interactive page load
    would after 15 s. Distinct from BasicDownloader's HTTP ``timeout``.
    """

    def __init__(
        self,
        url: str,
        headers: dict | None = None,
        timeout: float | None = None,
        *,
        rate_limit_timeout: float = 15.0,
    ):
        super().__init__(url, headers=headers, timeout=timeout)
        self._rate_limit_timeout = rate_limit_timeout

    def download(self):
        musicbrainz_limiter().acquire(timeout=self._rate_limit_timeout)
        return super().download()


_ARTIST_URL_FMT = "https://musicbrainz.org/artist/{}"
# MusicBrainz artist types that are organizations rather than individuals.
_ORG_ARTIST_TYPES = {"Group", "Orchestra", "Choir"}


def _extract_artist_credits(
    data: Dict[str, Any],
) -> tuple[list[str], list[Dict[str, Any]]]:
    """Read MusicBrainz ``artist-credit`` into display names and People links.

    Returns ``(artist_names, related_resources)``. ``related_resources`` is the
    list of ``{model: "People", id_type, id_value, url, title}`` entries the
    auto-fetch pipeline turns into ``MusicBrainzArtist`` resources.
    """
    artist_names: list[str] = []
    related: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for credit in data.get("artist-credit") or []:
        if isinstance(credit, dict) and "artist" in credit:
            artist = credit["artist"] or {}
            name = artist.get("name") or ""
            if name:
                artist_names.append(name)
            mbid = artist.get("id")
            if mbid and mbid not in seen:
                seen.add(mbid)
                related.append(
                    {
                        "model": "People",
                        "id_type": IdType.MusicBrainz_Artist,
                        "id_value": mbid,
                        "url": _ARTIST_URL_FMT.format(mbid),
                        "title": name,
                    }
                )
        elif isinstance(credit, str):
            artist_names.append(credit)
    return artist_names, related


def _extract_first_isrc(release_data: Dict[str, Any]) -> str | None:
    """Return the first ISRC found across the release's recordings.

    Albums don't have ISRCs in the strict sense (the code identifies a track),
    but the rest of the codebase (Spotify, Douban) stores a single album-level
    ISRC as a soft lookup key. Picking the first track's first ISRC matches
    that convention.
    """
    for medium in release_data.get("media") or []:
        for track in medium.get("tracks") or []:
            recording = track.get("recording") or {}
            for isrc in recording.get("isrcs") or []:
                if isrc:
                    return isrc
    return None


@SiteManager.register
class MusicBrainzReleaseGroup(AbstractSite):
    SITE_NAME = SiteName.MusicBrainz
    ID_TYPE = IdType.MusicBrainz_ReleaseGroup
    URL_PATTERNS = [
        r"^\w+://musicbrainz\.org/release-group/([a-f0-9\-]{36}).*",
    ]
    WIKI_PROPERTY_ID = "P436"  # MusicBrainz release group ID
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://musicbrainz.org/release-group/{id_value}"

    def get_api_headers(self):
        return {
            "User-Agent": settings.NEODB_USER_AGENT,
            "Accept": "application/json",
        }

    def scrape(self):
        """Scrape MusicBrainz data for release-group"""
        if not self.id_value:
            raise ParseError(self, "No MusicBrainz ID found")

        api_url = f"https://musicbrainz.org/ws/2/release-group/{self.id_value}?fmt=json&inc=artists+releases+tags+genres"
        headers = self.get_api_headers()

        try:
            downloader = MusicBrainzDownloader(api_url, headers=headers)
            response_data = downloader.download().json()
        except Exception as e:
            logger.error(f"Failed to fetch MusicBrainz data: {e}")
            raise ParseError(self, f"Failed to fetch data from MusicBrainz API: {e}")

        return self._parse_release_group_data(response_data)

    def _parse_release_group_data(self, data: Dict[str, Any]) -> ResourceContent:
        """Parse MusicBrainz release-group data into ResourceContent"""

        # Extract basic information
        title = data.get("title", "")
        if not title:
            raise ParseError(self, "No title found in MusicBrainz data")

        # Detect language and create localized title
        lang = detect_language(title)
        localized_title = [{"lang": lang, "text": title}]

        # Extract artists
        artists, related_artists = _extract_artist_credits(data)

        # Extract release date from first release if available
        release_date = None
        if "releases" in data and data["releases"]:
            first_release = data["releases"][0]
            release_date = first_release.get("date")

        # Extract genres and tags
        genres = []
        if "genres" in data:
            genres.extend([genre["name"] for genre in data["genres"]])
        if "tags" in data:
            # Add high-score tags as genres
            genres.extend(
                [tag["name"] for tag in data["tags"] if tag.get("count", 0) > 0]
            )

        # Get additional metadata from first release
        track_list = None
        duration = None
        company = []
        cover_image_url = None
        isrc = None

        if "releases" in data and data["releases"]:
            # Get the first release for additional details
            first_release = data["releases"][0]
            release_id = first_release["id"]

            # Fetch detailed release information
            try:
                release_data = self._get_release_details(release_id)
                if release_data:
                    track_info = self._extract_track_info(release_data)
                    track_list = track_info["track_list"]
                    duration = track_info["duration"]
                    company = self._extract_label_info(release_data)
                    cover_image_url = self._get_cover_art_url(release_id)
                    isrc = _extract_first_isrc(release_data)
            except Exception as e:
                logger.warning(f"Failed to get detailed release info: {e}")

        metadata = {
            "title": title,
            "localized_title": localized_title,
            "artist": artists,
            "genre": list(set(genres)),  # Remove duplicates
            "release_date": release_date,
            "brief": None,
        }

        if track_list:
            metadata["track_list"] = track_list
        if duration:
            metadata["duration"] = duration
        if company:
            metadata["company"] = company
        if cover_image_url:
            metadata["cover_image_url"] = cover_image_url
        if related_artists:
            metadata["related_resources"] = related_artists

        pd = ResourceContent(metadata=metadata)

        # GTIN from the first release that carries a usable barcode. Accept
        # both 12-digit UPC (left-padded to GTIN-13) and 13-digit EAN.
        for release in data.get("releases") or []:
            barcode = (release.get("barcode") or "").strip()
            if not barcode or not barcode.isdigit():
                continue
            if len(barcode) == 12:
                pd.lookup_ids[IdType.GTIN] = self._upc_to_gtin_13(barcode)
                break
            if len(barcode) == 13:
                pd.lookup_ids[IdType.GTIN] = barcode
                break
        if isrc:
            pd.lookup_ids[IdType.ISRC] = isrc

        return pd

    def _get_release_details(self, release_id: str) -> Dict[str, Any]:
        """Get detailed release information including tracks"""
        api_url = f"https://musicbrainz.org/ws/2/release/{release_id}?fmt=json&inc=recordings+labels+media+isrcs"
        headers = self.get_api_headers()
        downloader = MusicBrainzDownloader(api_url, headers=headers)
        return downloader.download().json()

    def _extract_track_info(self, release_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract track listing and duration from release data"""
        track_list = []
        total_duration = 0

        if "media" in release_data:
            for medium in release_data["media"]:
                if "tracks" in medium:
                    disc_num = medium.get("position", 1)
                    for track in medium["tracks"]:
                        track_num = track.get("position", len(track_list) + 1)
                        track_title = track.get("title", "Unknown Track")

                        # Add disc number if multiple discs
                        if len(release_data["media"]) > 1:
                            track_entry = f"{disc_num}-{track_num}. {track_title}"
                        else:
                            track_entry = f"{track_num}. {track_title}"

                        track_list.append(track_entry)

                        # Add duration if available (in milliseconds)
                        if "length" in track:
                            total_duration += int(track["length"])

        return {
            "track_list": "\n".join(track_list) if track_list else None,
            "duration": total_duration if total_duration > 0 else None,
        }

    def _extract_label_info(self, release_data: Dict[str, Any]) -> List[str]:
        """Extract label/company information from release data"""
        labels = []
        if "label-info" in release_data:
            for label_info in release_data["label-info"]:
                if "label" in label_info and "name" in label_info["label"]:
                    labels.append(label_info["label"]["name"])
        return labels

    def _get_cover_art_url(self, release_id: str) -> str | None:
        """Get cover art URL from Cover Art Archive"""
        try:
            cover_api_url = f"https://coverartarchive.org/release/{release_id}"
            headers = self.get_api_headers()

            downloader = BasicDownloader(cover_api_url, headers=headers)
            cover_data = downloader.download().json()

            if "images" in cover_data and cover_data["images"]:
                # Find front cover or use first image
                for image in cover_data["images"]:
                    if image.get("front", False):
                        return image.get("image", "")
                # If no front cover found, use first image
                return cover_data["images"][0].get("image", "")
        except Exception as e:
            logger.debug(f"No cover art found for release {release_id}: {e}")

        return None

    def _upc_to_gtin_13(self, upc: str) -> str:
        """Convert UPC-12 to GTIN-13 by adding leading zero"""
        if len(upc) == 12 and upc.isdigit():
            return "0" + upc
        return upc


@SiteManager.register
class MusicBrainzRelease(AbstractSite):
    SITE_NAME = SiteName.MusicBrainz
    ID_TYPE = IdType.MusicBrainz_Release
    URL_PATTERNS = [
        r"^\w+://musicbrainz\.org/release/([a-f0-9\-]{36}).*",
    ]
    WIKI_PROPERTY_ID = "P5813"  # MusicBrainz release ID
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://musicbrainz.org/release/{id_value}"

    def get_api_headers(self):
        return {
            "User-Agent": settings.NEODB_USER_AGENT,
            "Accept": "application/json",
        }

    def scrape(self):
        """Scrape MusicBrainz data for individual release"""
        if not self.id_value:
            raise ParseError(self, "No MusicBrainz release ID found")

        api_url = f"https://musicbrainz.org/ws/2/release/{self.id_value}?fmt=json&inc=artists+recordings+labels+media+release-groups+tags+genres+isrcs"
        headers = self.get_api_headers()

        try:
            downloader = MusicBrainzDownloader(api_url, headers=headers)
            response_data = downloader.download().json()
        except Exception as e:
            logger.error(f"Failed to fetch MusicBrainz release data: {e}")
            raise ParseError(self, f"Failed to fetch data from MusicBrainz API: {e}")

        return self._parse_release_data(response_data)

    def _parse_release_data(self, data: Dict[str, Any]) -> ResourceContent:
        """Parse MusicBrainz release data into ResourceContent"""

        # Extract basic information
        title = data.get("title", "")
        if not title:
            raise ParseError(self, "No title found in MusicBrainz release data")

        # Detect language and create localized title
        lang = detect_language(title)
        localized_title = [{"lang": lang, "text": title}]

        # Extract artists
        artists, related_artists = _extract_artist_credits(data)

        # Extract release date
        release_date = data.get("date")

        # Extract genres and tags from release and release-group
        genres = []
        if "genres" in data:
            genres.extend([genre["name"] for genre in data["genres"]])
        if "tags" in data:
            genres.extend(
                [tag["name"] for tag in data["tags"] if tag.get("count", 0) > 0]
            )

        # Also get genres from release-group if available
        if "release-group" in data:
            rg = data["release-group"]
            if "genres" in rg:
                genres.extend([genre["name"] for genre in rg["genres"]])
            if "tags" in rg:
                genres.extend(
                    [tag["name"] for tag in rg["tags"] if tag.get("count", 0) > 0]
                )

        # Extract track information and duration
        track_info = self._extract_track_info(data)
        track_list = track_info["track_list"]
        duration = track_info["duration"]

        # Extract label information
        company = self._extract_label_info(data)

        # Get cover art
        cover_image_url = self._get_cover_art_url(self.id_value)

        metadata = {
            "title": title,
            "localized_title": localized_title,
            "artist": artists,
            "genre": list(set(genres)),  # Remove duplicates
            "release_date": release_date,
            "brief": None,
        }

        if track_list:
            metadata["track_list"] = track_list
        if duration:
            metadata["duration"] = duration
        if company:
            metadata["company"] = company
        if cover_image_url:
            metadata["cover_image_url"] = cover_image_url
        if related_artists:
            metadata["related_resources"] = related_artists

        pd = ResourceContent(metadata=metadata)

        # Add lookup IDs for barcode/GTIN if available
        barcode = (data.get("barcode") or "").strip()
        if barcode and barcode.isdigit():
            if len(barcode) == 12:
                pd.lookup_ids[IdType.GTIN] = self._upc_to_gtin_13(barcode)
            elif len(barcode) == 13:
                pd.lookup_ids[IdType.GTIN] = barcode

        isrc = _extract_first_isrc(data)
        if isrc:
            pd.lookup_ids[IdType.ISRC] = isrc

        return pd

    def _extract_track_info(self, release_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract track listing and duration from release data"""
        track_list = []
        total_duration = 0

        if "media" in release_data:
            for medium in release_data["media"]:
                if "tracks" in medium:
                    disc_num = medium.get("position", 1)
                    for track in medium["tracks"]:
                        track_num = track.get("position", len(track_list) + 1)
                        track_title = track.get("title", "Unknown Track")

                        # Add disc number if multiple discs
                        if len(release_data["media"]) > 1:
                            track_entry = f"{disc_num}-{track_num}. {track_title}"
                        else:
                            track_entry = f"{track_num}. {track_title}"

                        track_list.append(track_entry)

                        # Add duration if available (in milliseconds)
                        if "length" in track:
                            total_duration += int(track["length"])

        return {
            "track_list": "\n".join(track_list) if track_list else None,
            "duration": total_duration if total_duration > 0 else None,
        }

    def _extract_label_info(self, release_data: Dict[str, Any]) -> List[str]:
        """Extract label/company information from release data"""
        labels = []
        if "label-info" in release_data:
            for label_info in release_data["label-info"]:
                if "label" in label_info and "name" in label_info["label"]:
                    labels.append(label_info["label"]["name"])
        return labels

    def _get_cover_art_url(self, release_id) -> str | None:
        """Get cover art URL from Cover Art Archive"""
        try:
            cover_api_url = f"https://coverartarchive.org/release/{release_id}"
            headers = self.get_api_headers()

            downloader = BasicDownloader(cover_api_url, headers=headers)
            cover_data = downloader.download().json()

            if "images" in cover_data and cover_data["images"]:
                # Find front cover or use first image
                for image in cover_data["images"]:
                    if image.get("front", False):
                        return image.get("image", "")
                # If no front cover found, use first image
                return cover_data["images"][0].get("image", "")
        except Exception as e:
            logger.debug(f"No cover art found for release {release_id}: {e}")

        return None

    def _upc_to_gtin_13(self, upc: str) -> str:
        """Convert UPC-12 to GTIN-13 by adding leading zero"""
        if len(upc) == 12 and upc.isdigit():
            return "0" + upc
        return upc

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        """Search MusicBrainz for releases"""
        if category not in ["music", "all"]:
            return []

        results = []
        api_url = "https://musicbrainz.org/ws/2/release"
        params = {
            "query": q,
            "fmt": "json",
            "limit": page_size,
            "offset": page * page_size,
        }

        headers = {
            "User-Agent": getattr(settings, "NEODB_USER_AGENT", "NeoDBApp/1.0"),
            "Accept": "application/json",
        }

        async with httpx.AsyncClient() as client:
            try:
                await musicbrainz_limiter().acquire_async()
                response = await client.get(
                    api_url, params=params, headers=headers, timeout=10
                )
                response.raise_for_status()
                data = response.json()

                if "releases" in data:
                    for release in data["releases"]:
                        title = release.get("title", "")
                        release_id = release.get("id", "")

                        if not title or not release_id:
                            continue

                        # Build subtitle with artist and date
                        subtitle_parts = []
                        if "artist-credit" in release:
                            artist_names = []
                            for credit in release["artist-credit"]:
                                if isinstance(credit, dict) and "artist" in credit:
                                    artist_names.append(credit["artist"]["name"])
                                elif isinstance(credit, str):
                                    artist_names.append(credit)
                            if artist_names:
                                subtitle_parts.append(" / ".join(artist_names))

                        if "date" in release and release["date"]:
                            subtitle_parts.append(release["date"][:4])  # Just year

                        subtitle = " · ".join(subtitle_parts)
                        url = cls.id_to_url(release_id)

                        results.append(
                            ExternalSearchResultItem(
                                ItemCategory.Music,
                                SiteName.MusicBrainz,
                                url,
                                title,
                                subtitle,
                                "",
                                "",  # No cover in search results
                            )
                        )

            except httpx.TimeoutException:
                logger.warning("MusicBrainz release search timeout", extra={"query": q})
                record_search_failure(SiteName.MusicBrainz.value, "timeout")
            except Exception as e:
                logger.error(
                    "MusicBrainz release search error",
                    extra={"query": q, "exception": e},
                )
                record_search_failure(SiteName.MusicBrainz.value, "error")

        return results

    @staticmethod
    def _escape_lucene(s: str) -> str:
        """Escape Lucene query-syntax special characters."""
        for ch in r'\+-&|!(){}[]^"~*?:/':
            s = s.replace(ch, "\\" + ch)
        return s

    @classmethod
    def build_field_query(cls, album: str, artist: str, year: str | None = None) -> str:
        """Build a Lucene field-scoped query for MusicBrainz release search."""
        parts = []
        if album:
            parts.append(f'release:"{cls._escape_lucene(album)}"')
        if artist:
            parts.append(f'artist:"{cls._escape_lucene(artist)}"')
        if year and str(year).isdigit():
            parts.append(f"date:{year}")
        return " AND ".join(parts)

    @classmethod
    async def search_by_fields(
        cls,
        album: str,
        artist: str,
        year: str | None = None,
        limit: int = 5,
    ) -> list[ExternalSearchResultItem]:
        """Search MusicBrainz releases by separate (album, artist, year) fields."""
        q = cls.build_field_query(album, artist, year)
        if not q:
            return []
        results: list[ExternalSearchResultItem] = []
        params = {"query": q, "fmt": "json", "limit": limit}
        headers = {
            "User-Agent": getattr(settings, "NEODB_USER_AGENT", "NeoDBApp/1.0"),
            "Accept": "application/json",
        }
        async with httpx.AsyncClient() as client:
            try:
                # search_by_fields is called from RYM import, which can issue
                # thousands of these in a row. Wait minutes for our slot
                # rather than falling open and bursting MB.
                await musicbrainz_limiter().acquire_async(timeout=300.0)
                response = await client.get(
                    "https://musicbrainz.org/ws/2/release",
                    params=params,
                    headers=headers,
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
            except httpx.TimeoutException:
                logger.warning("MusicBrainz field search timeout", extra={"query": q})
                record_search_failure(SiteName.MusicBrainz.value, "timeout")
                return results
            except Exception as e:
                logger.error(
                    "MusicBrainz field search error",
                    extra={"query": q, "exception": e},
                )
                record_search_failure(SiteName.MusicBrainz.value, "error")
                return results
            for release in data.get("releases", []) or []:
                title = release.get("title", "")
                rid = release.get("id", "")
                if not title or not rid:
                    continue
                subtitle_parts = []
                names = []
                for credit in release.get("artist-credit", []) or []:
                    if isinstance(credit, dict) and "artist" in credit:
                        names.append(credit["artist"].get("name", ""))
                    elif isinstance(credit, str):
                        names.append(credit)
                names = [n for n in names if n]
                if names:
                    subtitle_parts.append(" / ".join(names))
                if release.get("date"):
                    subtitle_parts.append(release["date"][:4])
                results.append(
                    ExternalSearchResultItem(
                        ItemCategory.Music,
                        SiteName.MusicBrainz,
                        cls.id_to_url(rid),
                        title,
                        " · ".join(subtitle_parts),
                        "",
                        "",
                    )
                )
        return results


@SiteManager.register
class MusicBrainzArtist(AbstractSite):
    SITE_NAME = SiteName.MusicBrainz
    ID_TYPE = IdType.MusicBrainz_Artist
    URL_PATTERNS = [
        r"^\w+://musicbrainz\.org/artist/([a-f0-9\-]{36}).*",
    ]
    WIKI_PROPERTY_ID = "P434"  # MusicBrainz artist ID
    DEFAULT_MODEL = People

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://musicbrainz.org/artist/{id_value}"

    def get_api_headers(self):
        return {
            "User-Agent": settings.NEODB_USER_AGENT,
            "Accept": "application/json",
        }

    def scrape(self):
        if not self.id_value:
            raise ParseError(self, "No MusicBrainz artist ID found")

        api_url = (
            f"https://musicbrainz.org/ws/2/artist/{self.id_value}"
            "?fmt=json&inc=aliases+url-rels"
        )
        try:
            # Artist scrapes mostly arrive via the album related-resources
            # fan-out queued from `fetch_related_resources_task`, which is a
            # batch path: a 1000-album RYM import can stack hundreds of these
            # behind the 1 req/s cursor. A generous timeout keeps them on the
            # throttle instead of falling open and bursting MB.
            downloader = MusicBrainzDownloader(
                api_url,
                headers=self.get_api_headers(),
                rate_limit_timeout=300.0,
            )
            data = downloader.download().json()
        except Exception as e:
            logger.error(f"Failed to fetch MusicBrainz artist data: {e}")
            raise ParseError(
                self, f"Failed to fetch data from MusicBrainz API: {e}"
            ) from e

        return self._parse_artist_data(data)

    def _parse_artist_data(self, data: Dict[str, Any]) -> ResourceContent:
        name = (data.get("name") or "").strip()
        if not name:
            raise ParseError(self, "No name found in MusicBrainz artist data")

        # localized_name: start with the primary name, then merge in display
        # aliases that explicitly target another locale. Sort names and
        # MusicBrainz alias variants like "Search hint" / "Legal name" / "Sort
        # name" are deliberately excluded -- they pollute display_name lookups
        # and would let People.link_matching_credits link unrelated credits.
        localized_name: list[Dict[str, str]] = []
        seen_names: set[tuple[str, str]] = set()

        def _add_name(text: str, lang: str | None) -> None:
            text = (text or "").strip()
            if not text:
                return
            lang = (lang or "").strip() or detect_language(text)
            key = (lang, text)
            if key in seen_names:
                return
            seen_names.add(key)
            localized_name.append({"lang": lang, "text": text})

        _add_name(name, None)
        for alias in data.get("aliases") or []:
            if not isinstance(alias, dict):
                continue
            alias_type = (alias.get("type") or "").strip()
            # Only accept display-name aliases. MB's "Artist name" type (and
            # entries without a type) are display variants; everything else
            # (Sort name, Search hint, Legal name) is metadata-only.
            if alias_type and alias_type != "Artist name":
                continue
            locale = (alias.get("locale") or "").strip()
            # Require an explicit locale so we never invent a language tag for
            # an alias whose intended scope MB did not specify.
            if not locale:
                continue
            _add_name(alias.get("name") or "", locale)

        # Bio: MusicBrainz exposes only the disambiguation blurb on the artist
        # endpoint; richer bios live in linked Wikipedia entries which we don't
        # crawl here.
        localized_bio: list[Dict[str, str]] = []
        disambiguation = (data.get("disambiguation") or "").strip()
        if disambiguation:
            localized_bio.append(
                {"lang": detect_language(disambiguation), "text": disambiguation}
            )

        # type maps onto PeopleType: Group/Orchestra/Choir => organization,
        # Person/Character/Other/missing => person.
        mb_type = (data.get("type") or "").strip()
        people_type = (
            PeopleType.ORGANIZATION.value
            if mb_type in _ORG_ARTIST_TYPES
            else PeopleType.PERSON.value
        )

        life_span = data.get("life-span") or {}
        birth_date = (life_span.get("begin") or "").strip() or None
        death_date = (life_span.get("end") or "").strip() or None

        official_site = None
        wikidata_qid = None
        for rel in data.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            url_obj = rel.get("url") or {}
            resource = url_obj.get("resource") or ""
            rel_type = rel.get("type") or ""
            if rel_type == "official homepage" and not official_site:
                official_site = resource
            elif rel_type == "wikidata" and not wikidata_qid:
                # Wikidata relations look like https://www.wikidata.org/wiki/Q123.
                # Anchor on the canonical /wiki/ path and force ASCII digits so
                # we don't pick up an unrelated "/Q\d+" earlier in the URL or
                # match non-ASCII numerics that Python's \d would accept.
                m = re.match(
                    r"https?://(?:www\.)?wikidata\.org/(?:wiki|entity)/(Q[0-9]+)",
                    resource or "",
                )
                if m:
                    wikidata_qid = m.group(1)

        metadata: Dict[str, Any] = {
            "title": name,
            "localized_name": localized_name,
            "localized_bio": localized_bio,
            "people_type": people_type,
            "birth_date": birth_date,
            "death_date": death_date,
            "official_site": official_site,
            "cover_image_url": None,
        }

        pd = ResourceContent(metadata=metadata)
        if wikidata_qid:
            pd.lookup_ids[IdType.WikiData] = wikidata_qid
        return pd

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in ["people", "all"]:
            return []

        results: list[ExternalSearchResultItem] = []
        params = {
            "query": q,
            "fmt": "json",
            "limit": page_size,
            "offset": page * page_size,
        }
        headers = {
            "User-Agent": settings.NEODB_USER_AGENT,
            "Accept": "application/json",
        }
        async with httpx.AsyncClient() as client:
            try:
                await musicbrainz_limiter().acquire_async()
                response = await client.get(
                    "https://musicbrainz.org/ws/2/artist",
                    params=params,
                    headers=headers,
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
            except httpx.TimeoutException:
                logger.warning("MusicBrainz artist search timeout", extra={"query": q})
                record_search_failure(SiteName.MusicBrainz.value, "timeout")
                return results
            except Exception as e:
                logger.error(
                    "MusicBrainz artist search error",
                    extra={"query": q, "exception": e},
                )
                record_search_failure(SiteName.MusicBrainz.value, "error")
                return results

            for artist in data.get("artists", []) or []:
                name = artist.get("name") or ""
                mbid = artist.get("id") or ""
                if not name or not mbid:
                    continue
                subtitle_parts = []
                if artist.get("disambiguation"):
                    subtitle_parts.append(artist["disambiguation"])
                if artist.get("type"):
                    subtitle_parts.append(artist["type"])
                if artist.get("country"):
                    subtitle_parts.append(artist["country"])
                results.append(
                    ExternalSearchResultItem(
                        ItemCategory.People,
                        SiteName.MusicBrainz,
                        cls.id_to_url(mbid),
                        name,
                        " · ".join(subtitle_parts),
                        "",
                        "",
                    )
                )
        return results
