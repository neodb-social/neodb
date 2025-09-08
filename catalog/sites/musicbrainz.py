"""
MusicBrainz

MusicBrainz is an open music encyclopedia that collects music metadata and makes it available to the public.
Using the MusicBrainz API to fetch album/release-group information.
"""

import logging
from typing import Any, Dict, List

import httpx
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.models import *
from catalog.search import ExternalSearchResultItem
from common.models.lang import detect_language

_logger = logging.getLogger(__name__)


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
            downloader = BasicDownloader(api_url, headers=headers)
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
        artists = []
        if "artist-credit" in data:
            for credit in data["artist-credit"]:
                if isinstance(credit, dict) and "artist" in credit:
                    artists.append(credit["artist"]["name"])
                elif isinstance(credit, str):
                    artists.append(credit)

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

        pd = ResourceContent(metadata=metadata)

        # Add lookup IDs for any ISRCs or other identifiers found
        if "releases" in data:
            for release in data["releases"]:
                # Could add barcode/EAN as GTIN if available
                if "barcode" in release and release["barcode"]:
                    try:
                        # Convert barcode to GTIN-13 if it's 12 digits (UPC)
                        barcode = release["barcode"]
                        if len(barcode) == 12 and barcode.isdigit():
                            gtin = self._upc_to_gtin_13(barcode)
                            if gtin:
                                pd.lookup_ids[IdType.GTIN] = gtin
                    except Exception:
                        pass

        return pd

    def _get_release_details(self, release_id: str) -> Dict[str, Any]:
        """Get detailed release information including tracks"""
        api_url = f"https://musicbrainz.org/ws/2/release/{release_id}?fmt=json&inc=recordings+labels+media"
        headers = self.get_api_headers()
        downloader = BasicDownloader(api_url, headers=headers)
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

    # @classmethod
    # async def search_task(
    #     cls, q: str, page: int, category: str, page_size: int
    # ) -> list[ExternalSearchResultItem]:
    #     """Search MusicBrainz for release-groups"""
    #     if category not in ["music", "all"]:
    #         return []

    #     results = []
    #     api_url = "https://musicbrainz.org/ws/2/release-group"
    #     params = {
    #         "query": q,
    #         "fmt": "json",
    #         "limit": page_size,
    #         "offset": page * page_size,
    #     }

    #     headers = {
    #         "User-Agent": getattr(settings, "NEODB_USER_AGENT", "NeoDBApp/1.0"),
    #         "Accept": "application/json",
    #     }

    #     async with httpx.AsyncClient() as client:
    #         try:
    #             response = await client.get(
    #                 api_url, params=params, headers=headers, timeout=10
    #             )
    #             response.raise_for_status()
    #             data = response.json()

    #             if "release-groups" in data:
    #                 for rg in data["release-groups"]:
    #                     title = rg.get("title", "")
    #                     rg_id = rg.get("id", "")

    #                     if not title or not rg_id:
    #                         continue

    #                     # Build subtitle with artist and date
    #                     subtitle_parts = []
    #                     if "artist-credit" in rg:
    #                         artist_names = []
    #                         for credit in rg["artist-credit"]:
    #                             if isinstance(credit, dict) and "artist" in credit:
    #                                 artist_names.append(credit["artist"]["name"])
    #                             elif isinstance(credit, str):
    #                                 artist_names.append(credit)
    #                         if artist_names:
    #                             subtitle_parts.append(" / ".join(artist_names))

    #                     if "first-release-date" in rg and rg["first-release-date"]:
    #                         subtitle_parts.append(
    #                             rg["first-release-date"][:4]
    #                         )  # Just year

    #                     subtitle = " · ".join(subtitle_parts)
    #                     url = cls.id_to_url(rg_id)

    #                     results.append(
    #                         ExternalSearchResultItem(
    #                             ItemCategory.Music,
    #                             SiteName.MusicBrainz,
    #                             url,
    #                             title,
    #                             subtitle,
    #                             "",
    #                             "",  # No cover in search results
    #                         )
    #                     )

    #         except httpx.TimeoutException:
    #             logger.warning("MusicBrainz search timeout", extra={"query": q})
    #         except Exception as e:
    #             logger.error(
    #                 "MusicBrainz search error", extra={"query": q, "exception": e}
    #             )

    #     return results


@SiteManager.register
class MusicBrainzRelease(AbstractSite):
    SITE_NAME = SiteName.MusicBrainz
    ID_TYPE = IdType.MusicBrainz_Release
    URL_PATTERNS = [
        r"^\w+://musicbrainz\.org/release/([a-f0-9\-]{36}).*",
    ]
    WIKI_PROPERTY_ID = "P437"  # MusicBrainz release ID
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

        api_url = f"https://musicbrainz.org/ws/2/release/{self.id_value}?fmt=json&inc=artists+recordings+labels+media+release-groups+tags+genres"
        headers = self.get_api_headers()

        try:
            downloader = BasicDownloader(api_url, headers=headers)
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
        artists = []
        if "artist-credit" in data:
            for credit in data["artist-credit"]:
                if isinstance(credit, dict) and "artist" in credit:
                    artists.append(credit["artist"]["name"])
                elif isinstance(credit, str):
                    artists.append(credit)

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

        pd = ResourceContent(metadata=metadata)

        # Add lookup IDs for barcode/GTIN if available
        if "barcode" in data and data["barcode"]:
            try:
                barcode = data["barcode"]
                if len(barcode) == 12 and barcode.isdigit():
                    gtin = self._upc_to_gtin_13(barcode)
                    if gtin:
                        pd.lookup_ids[IdType.GTIN] = gtin
                elif len(barcode) == 13 and barcode.isdigit():
                    pd.lookup_ids[IdType.GTIN] = barcode
            except Exception:
                pass

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
            except Exception as e:
                logger.error(
                    "MusicBrainz release search error",
                    extra={"query": q, "exception": e},
                )

        return results
