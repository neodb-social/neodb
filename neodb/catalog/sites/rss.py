import logging
import pickle
import urllib.error
import urllib.request
from datetime import datetime

import bleach
import podcastparser
from django.conf import settings
from django.core.cache import cache
from django.core.validators import URLValidator
from django.utils.timezone import make_aware
from loguru import logger

from catalog.common import *
from catalog.common.downloaders import (
    _local_response_path,
    get_mock_file,
    get_mock_mode,
)
from catalog.models import IdType, Podcast, PodcastEpisode, SiteName
from common.models.lang import detect_language
from common.validators import is_valid_url
from journal.models.renderers import html_to_text

_logger = logging.getLogger(__name__)


@SiteManager.register
class RSS(AbstractSite):
    SITE_NAME = SiteName.RSS
    ID_TYPE = IdType.RSS
    DEFAULT_MODEL = Podcast
    URL_PATTERNS = [r".+[./](rss|xml)"]

    @staticmethod
    def _open_feed(url: str, etag: str = "", last_modified: str = ""):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", settings.NEODB_USER_AGENT)
        if etag:
            req.add_header("If-None-Match", etag)
        if last_modified:
            req.add_header("If-Modified-Since", last_modified)
        return urllib.request.urlopen(req, timeout=3)

    @staticmethod
    def fetch_feed_with_metadata(
        url: str, etag: str = "", last_modified: str = ""
    ) -> tuple[dict | None, str, str, int]:
        """Fetch and parse an RSS feed with conditional-GET support.

        Returns (feed, new_etag, new_last_modified, status):
          - status 200: feed parsed
          - status 304: not modified, feed is None
          - status 0:   network/parse error, feed is None
        """
        if not url or not is_valid_url(url):
            return None, etag, last_modified, 0
        if get_mock_mode():
            with open(_local_response_path + get_mock_file(url), "rb") as f:
                feed = pickle.load(f)
            return feed, etag, last_modified, 200
        try_urls = [url]
        if url.startswith("https://"):
            try_urls.append(url.replace("https://", "http://"))
        resp = None
        for try_url in try_urls:
            try:
                resp = RSS._open_feed(try_url, etag, last_modified)
                break
            except urllib.error.HTTPError as e:
                if e.code == 304:
                    return None, etag, last_modified, 304
                resp = None
            except Exception:
                resp = None
        if resp is None:
            return None, etag, last_modified, 0
        new_etag = resp.headers.get("ETag", "") or ""
        new_last_modified = resp.headers.get("Last-Modified", "") or ""
        try:
            feed = podcastparser.parse(url, resp)
        except Exception:
            return None, etag, last_modified, 0
        if settings.DOWNLOADER_SAVEDIR:
            with open(
                settings.DOWNLOADER_SAVEDIR + "/" + get_mock_file(url), "wb"
            ) as f:
                pickle.dump(feed, f)
        return feed, new_etag, new_last_modified, 200

    @staticmethod
    def parse_feed_from_url(url):
        if not url or not is_valid_url(url):
            return None
        cache_key = f"rss:{url}"
        feed = cache.get(cache_key)
        if feed:
            return feed
        feed, _e, _m, status = RSS.fetch_feed_with_metadata(url)
        if status != 200 or feed is None:
            return None
        cache.set(cache_key, feed, timeout=settings.DOWNLOADER_CACHE_TIMEOUT)
        return feed

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://{id_value}"

    @classmethod
    def url_to_id(cls, url: str):
        return url.split("://")[1]

    @classmethod
    def validate_url_fallback(cls, url):
        val = URLValidator()
        try:
            val(url)
            return cls.parse_feed_from_url(url) is not None
        except Exception:
            return False

    def scrape(self):
        if not self.url:
            raise ValueError("no url avaialble in RSS site")
        feed = self.parse_feed_from_url(self.url)
        if not feed:
            raise ValueError(f"no feed avaialble in {self.url}")
        title = feed["title"].strip()
        if not title:
            raise ParseError(self, "title")
        desc = html_to_text(feed.get("description") or "")
        lang = detect_language(title + " " + desc)
        pd = ResourceContent(
            metadata={
                "title": title,
                "brief": desc,
                "localized_title": [{"lang": lang, "text": title}],
                "localized_description": [{"lang": lang, "text": desc}] if desc else [],
                "host": (
                    [feed.get("itunes_author")] if feed.get("itunes_author") else []
                ),
                "official_site": feed.get("link"),
                "cover_image_url": feed.get("cover_url"),
                "genre": [
                    item
                    for cat in feed.get("itunes_categories", [])
                    for item in (cat if isinstance(cat, list) else [cat])
                    if item
                ],
            }
        )
        pd.lookup_ids[IdType.RSS] = RSS.url_to_id(self.url)
        return pd

    @staticmethod
    def update_episodes_from_feed(podcast: Podcast, feed: dict) -> int:
        """Insert any new episodes from a parsed feed; return count added."""
        episodes = feed.get("episodes") or []
        if not episodes:
            return 0
        # Batch-fetch existing episodes to avoid N+1 get_or_create queries
        guids = [ep.get("guid") for ep in episodes if ep.get("guid")]
        existing = (
            set(
                PodcastEpisode.objects.filter(
                    program=podcast, guid__in=guids
                ).values_list("guid", flat=True)
            )
            if guids
            else set()
        )
        added = 0
        for episode in episodes:
            guid = episode.get("guid")
            if guid and guid in existing:
                continue
            enclosures = episode.get("enclosures") or []
            media_url = enclosures[0].get("url") if enclosures else None
            if not media_url:
                continue
            _, created = PodcastEpisode.objects.get_or_create(
                program=podcast,
                guid=guid,
                defaults={
                    "title": episode["title"],
                    "brief": bleach.clean(episode.get("description") or "", strip=True),
                    "description_html": episode.get("description_html"),
                    "cover_url": episode.get("episode_art_url"),
                    "media_url": media_url,
                    "pub_date": (
                        make_aware(datetime.fromtimestamp(episode["published"]))
                        if episode.get("published") is not None
                        else None
                    ),
                    "duration": episode.get("duration"),
                    "link": episode.get("link"),
                },
            )
            if created:
                added += 1
        return added

    def scrape_additional_data(self):
        feed = self.parse_feed_from_url(self.url)
        if not feed:
            logger.warning(f"unable to parse RSS {self.url}")
            return False
        item = self.get_item()
        if not item:
            logger.warning(f"item for RSS {self.url} not found")
            return False
        self.update_episodes_from_feed(item, feed)
        return True
