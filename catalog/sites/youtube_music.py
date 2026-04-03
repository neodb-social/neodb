"""
YouTube Music

Uses the YouTube Music Innertube API (no credentials required).

Canonical URL format (Wikidata P4300):
  https://music.youtube.com/playlist?list=OLAK5uy_<id>

Scraping strategy:
  Two Innertube API calls per album:
    1. Browse VL{OLAK5uy_id} → resolve the internal MPREb_ album browse ID
    2. Browse MPREb_ → fetch title, artist, year, track list, cover image
  Mock mode: reads fixture keyed to self.url (contains the MPREb_ browse response).
"""

import re

import requests
from django.conf import settings
from loguru import logger

from catalog.common import *
from catalog.common.downloaders import get_mock_mode
from catalog.models import *
from common.models.lang import detect_language

_INNERTUBE_URL = "https://music.youtube.com/youtubei/v1/browse"
_INNERTUBE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:107.0) Gecko/20100101 Firefox/107.0",
    "Content-Type": "application/json",
    "Referer": "https://music.youtube.com/",
    "X-YouTube-Client-Name": "67",
    "X-YouTube-Client-Version": "1.20250101.01.00",
    "Origin": "https://music.youtube.com",
}
_INNERTUBE_CONTEXT = {
    "client": {
        "clientName": "WEB_REMIX",
        "clientVersion": "1.20250101.01.00",
        "hl": "en",
        "gl": "US",
    }
}


def _innertube_browse(browse_id: str) -> dict:
    resp = requests.post(
        _INNERTUBE_URL,
        json={"browseId": browse_id, "context": _INNERTUBE_CONTEXT},
        headers=_INNERTUBE_HEADERS,
        timeout=settings.DOWNLOADER_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_duration_ms(s: str) -> int:
    """Convert 'M:SS' or 'H:MM:SS' string to milliseconds."""
    parts = s.split(":")
    try:
        if len(parts) == 2:
            return (int(parts[0]) * 60 + int(parts[1])) * 1000
        if len(parts) == 3:
            return (int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])) * 1000
    except ValueError:
        pass
    return 0


def _largest_thumbnail_url(thumbnails: list) -> str | None:
    if not thumbnails:
        return None
    largest = max(thumbnails, key=lambda t: t.get("width", 0))
    url = largest["url"]
    return re.sub(r"=w\d+-h\d+.*", "=w576-h576-l90-rj", url)


def _get_mpreb_id(playlist_id: str) -> str | None:
    """Browse VL{OLAK5uy_} and extract the MPREb_ album browse ID from track data."""
    data = _innertube_browse("VL" + playlist_id)
    two_col = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {})
    sec_contents = (
        two_col.get("secondaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
    )
    tracks = (
        sec_contents[0].get("musicPlaylistShelfRenderer", {}).get("contents", [])
        if sec_contents
        else []
    )
    for track in tracks:
        renderer = track.get("musicResponsiveListItemRenderer", {})
        for col in renderer.get("flexColumns", []):
            for run in (
                col.get("musicResponsiveListItemFlexColumnRenderer", {})
                .get("text", {})
                .get("runs", [])
            ):
                ep = run.get("navigationEndpoint", {}).get("browseEndpoint", {})
                page_type = (
                    ep.get("browseEndpointContextSupportedConfigs", {})
                    .get("browseEndpointContextMusicConfig", {})
                    .get("pageType", "")
                )
                bid = ep.get("browseId", "")
                if page_type == "MUSIC_PAGE_TYPE_ALBUM" and bid.startswith("MPREb_"):
                    return bid
    logger.warning(
        f"YouTubeMusic: could not extract MPREb_ from VL{playlist_id}; "
        f"top-level keys: {list(data.get('contents', {}).keys())}"
    )
    return None


def _parse_mpreb_data(data: dict) -> ResourceContent:
    """Parse a MPREb_ browse API response into ResourceContent."""
    two_col = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {})

    # Header: title, artist, year, album type, cover
    tabs = two_col.get("tabs", [])
    sections = (
        tabs[0]
        .get("tabRenderer", {})
        .get("content", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
        if tabs
        else []
    )
    header = sections[0].get("musicResponsiveHeaderRenderer", {}) if sections else {}

    title_runs = header.get("title", {}).get("runs", [])
    title = title_runs[0].get("text", "") if title_runs else ""
    artist = [
        r.get("text", "")
        for r in header.get("straplineTextOne", {}).get("runs", [])
        if r.get("navigationEndpoint")
    ]
    subtitle_texts = [
        r.get("text", "") for r in header.get("subtitle", {}).get("runs", [])
    ]
    year = next(
        (
            t.strip()
            for t in subtitle_texts
            if t.strip().isdigit() and len(t.strip()) == 4
        ),
        None,
    )
    # YouTube Music only provides the release year; store as Jan 1 of that year
    # so the DateField receives a valid YYYY-MM-DD string rather than a bare year.
    release_date = f"{year}-01-01" if year else None
    album_type = subtitle_texts[0] if subtitle_texts else None
    thumbs = (
        header.get("thumbnail", {})
        .get("musicThumbnailRenderer", {})
        .get("thumbnail", {})
        .get("thumbnails", [])
    )
    cover_url = _largest_thumbnail_url(thumbs)

    # Tracks from secondaryContents
    sec2 = (
        two_col.get("secondaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
    )
    tracks_raw = (
        sec2[0].get("musicShelfRenderer", {}).get("contents", []) if sec2 else []
    )

    track_list = []
    total_ms = 0
    for t in tracks_raw:
        r = t.get("musicResponsiveListItemRenderer", {})
        idx = r.get("index", {}).get("runs", [{}])[0].get("text", "")
        flex = r.get("flexColumns", [])
        name = (
            flex[0]
            .get("musicResponsiveListItemFlexColumnRenderer", {})
            .get("text", {})
            .get("runs", [{}])[0]
            .get("text", "")
            if flex
            else ""
        )
        fixed = r.get("fixedColumns", [])
        dur_str = (
            fixed[0]
            .get("musicResponsiveListItemFixedColumnRenderer", {})
            .get("text", {})
            .get("runs", [{}])[0]
            .get("text", "")
            if fixed
            else ""
        )
        total_ms += _parse_duration_ms(dur_str)
        track_list.append(f"{idx}. {name}" if idx else name)

    lang = detect_language(title)
    return ResourceContent(
        metadata={
            "title": title,
            "localized_title": [{"lang": lang, "text": title}],
            "artist": artist,
            "release_date": release_date,
            "album_type": album_type,
            "track_list": "\n".join(track_list),
            "duration": total_ms if total_ms else None,
            "cover_image_url": cover_url,
        }
    )


@SiteManager.register
class YouTubeMusic(AbstractSite):
    SITE_NAME = SiteName.YouTubeMusic
    ID_TYPE = IdType.YouTubeMusic
    URL_PATTERNS = [
        r"https://music\.youtube\.com/playlist\?list=(OLAK5uy_[a-zA-Z0-9_-]+)",
    ]
    WIKI_PROPERTY_ID = "P4300"
    DEFAULT_MODEL = Album

    @classmethod
    def id_to_url(cls, id_value: str) -> str:
        return f"https://music.youtube.com/playlist?list={id_value}"

    def scrape(self) -> ResourceContent:
        if not self.url or not self.id_value:
            raise ParseError(self, "missing URL or ID")
        if get_mock_mode():
            # Fixture is keyed to self.url and contains the MPREb_ browse response.
            data = BasicDownloader(self.url).download().json()
            return _parse_mpreb_data(data)

        try:
            mpreb_id = _get_mpreb_id(self.id_value)
        except requests.exceptions.RequestException as e:
            raise ParseError(self, f"Innertube VL browse failed: {e}") from e
        if not mpreb_id:
            raise ParseError(
                self, "could not extract MPREb_ album ID from OLAK5uy_ playlist"
            )
        logger.debug(f"YouTubeMusic: resolved {self.id_value} -> {mpreb_id}")
        try:
            data = _innertube_browse(mpreb_id)
        except requests.exceptions.RequestException as e:
            raise ParseError(self, f"Innertube MPREb_ browse failed: {e}") from e
        return _parse_mpreb_data(data)

    def scrape_additional_data(self) -> bool:
        # Skip in mock/test mode — Wikidata SPARQL makes live network calls.
        if not self.resource or not self.id_value or get_mock_mode():
            return False

        from catalog.sites.wikidata import WikiData

        try:
            qid = WikiData.lookup_qid_by_external_id(IdType.YouTubeMusic, self.id_value)
        except Exception as e:
            logger.warning(
                f"YouTubeMusic Wikidata lookup failed for {self.id_value}: {e}"
            )
            return False
        if not qid:
            return False
        logger.debug(f"YouTubeMusic: found Wikidata {qid} for {self.id_value}")
        try:
            # Use WikiData private helpers to extract linked IDs without
            # triggering a full get_resource_ready() scrape cycle.
            wiki = WikiData(id_value=qid)
            entity_data = wiki._fetch_entity_by_id(qid)
            external_ids = wiki._extract_external_ids(entity_data)
        except Exception as e:
            logger.warning(f"YouTubeMusic Wikidata entity fetch failed for {qid}: {e}")
            return False
        new_ids: dict = {IdType.WikiData: qid}
        for ext_id in external_ids:
            if ext_id["id_type"] != IdType.YouTubeMusic:
                new_ids[ext_id["id_type"]] = ext_id["id_value"]
        existing = self.resource.other_lookup_ids or {}
        merged = {**new_ids, **existing}  # existing values take priority
        if merged != existing:
            self.resource.other_lookup_ids = merged
            self.resource.save(update_fields=["other_lookup_ids"])
        return True
