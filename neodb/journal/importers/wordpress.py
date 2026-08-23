import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from loguru import logger
from lxml import etree
from markdownify import markdownify as md

from catalog.common.downloaders import BasicImageDownloader
from journal.models import Article

from .base import BaseImporter

_NS = {
    "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wp": "http://wordpress.org/export/1.2/",
}

# Post types we import; everything else in a WXR file (pages, attachments once
# consumed as featured images, nav_menu_item, revisions, custom types) has no
# Article equivalent.
_IMPORTED_POST_TYPE = "post"

# Statuses that were never published and must not resurface as content.
_DISCARDED_STATUSES = {"trash", "auto-draft"}

# Only a WordPress "publish" maps to the visibility the user picked; a draft,
# pending or private post (or a password-protected one) becomes the most
# restrictive visibility so importing can never publish something that was
# unpublished on the source site.
_PUBLIC_STATUS = "publish"
_PRIVATE_VISIBILITY = 2


def _qname(prefix: str, tag: str) -> str:
    return f"{{{_NS[prefix]}}}{tag}"


def _parser() -> etree.XMLParser:
    """Parser for user-supplied XML: no entity expansion (billion laughs), no
    DTD loading, no network access for external references."""
    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )


def _text(element, tag: str) -> str:
    if element is None:
        return ""
    found = element.find(tag)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _parse_wp_date(value: str) -> datetime.datetime | None:
    """Parse a wp:post_date_gmt ('YYYY-MM-DD HH:MM:SS'). WordPress writes
    all-zeroes for a post that was never published."""
    if not value or value.startswith("0000"):
        return None
    try:
        return datetime.datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.UTC
        )
    except ValueError:
        return None


def _parse_pub_date(value: str) -> datetime.datetime | None:
    """Parse an RSS pubDate (RFC 2822), the fallback when post_date_gmt is
    missing or zeroed."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt


class WordpressImporter(BaseImporter):
    """Import WordPress posts from a WXR (WordPress eXtended RSS) file as
    standalone Articles.

    Only ``post`` items are imported. ``attachment`` items are consumed to
    resolve the featured image of the posts that reference them through a
    ``_thumbnail_id`` postmeta, which is how WordPress itself records it.
    """

    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        if not uploaded_file:
            return False
        try:
            tree = etree.parse(uploaded_file, _parser())
        except Exception:
            return False
        finally:
            try:
                uploaded_file.seek(0)
            except Exception:
                pass
        root = tree.getroot()
        if root is None or etree.QName(root).localname != "rss":
            return False
        channel = root.find("./channel")
        if channel is None:
            return False
        # A real WordPress export always declares its WXR version; accept a
        # hand-built file too as long as it carries wp-namespaced post types.
        if channel.find(_qname("wp", "wxr_version")) is not None:
            return True
        return channel.find(f"./item/{_qname('wp', 'post_type')}") is not None

    def _tags(self, item) -> list[str]:
        """Post tags and categories. WordPress separates the two, NeoDB has
        only tags, so both become tags (update_local_article normalizes and
        deduplicates them)."""
        tags = []
        for category in item.findall("category"):
            if category.get("domain") not in ("post_tag", "category"):
                continue
            name = (category.text or "").strip()
            if name:
                tags.append(name)
        return tags

    def _thumbnail_id(self, item) -> str:
        for meta in item.findall(_qname("wp", "postmeta")):
            if _text(meta, _qname("wp", "meta_key")) == "_thumbnail_id":
                return _text(meta, _qname("wp", "meta_value"))
        return ""

    def _fetch_cover(self, url: str) -> SimpleUploadedFile | None:
        """Download a featured image. Returns None on any failure -- a missing
        cover must never fail the whole article. ``download_image`` refuses
        non-public URLs itself, so a WXR file cannot use this to probe the
        internal network."""
        raw, ext = BasicImageDownloader.download_image(url, None, headers={})
        if not raw or not ext:
            logger.warning(f"unable to fetch featured image {url}")
            return None
        return SimpleUploadedFile(f"cover.{ext}", raw)

    def import_post(
        self, item, attachments: dict[str, str]
    ) -> BaseImporter.ImportResult:
        try:
            owner = self.user.identity
            status = _text(item, _qname("wp", "status"))
            if status in _DISCARDED_STATUSES:
                return "skipped"
            title = _text(item, "title")[:500]
            created = _parse_wp_date(
                _text(item, _qname("wp", "post_date_gmt"))
            ) or _parse_pub_date(_text(item, "pubDate"))
            protected = bool(_text(item, _qname("wp", "post_password")))
            if status == _PUBLIC_STATUS and not protected:
                visibility = self.metadata.get("visibility", 0)
            else:
                visibility = _PRIVATE_VISIBILITY
            # dedup before the featured-image fetch so re-importing the same
            # file does not re-download every cover
            duplicate = Article.objects.filter(owner=owner, title=title)
            duplicate = duplicate.filter(created_time=created) if created else duplicate
            if duplicate.exists():
                return "skipped"
            # markdownify drops the <!-- wp:paragraph --> block delimiters
            # Gutenberg bodies are wrapped in, so no pre-pass is needed
            body = md(_text(item, _qname("content", "encoded")))
            cover = None
            cover_url = attachments.get(self._thumbnail_id(item))
            if cover_url:
                cover = self._fetch_cover(cover_url)
            article = Article.update_local_article(
                owner=owner,
                title=title,
                body=body.strip(),
                summary=_text(item, _qname("excerpt", "encoded")),
                visibility=visibility,
                tags=self._tags(item),
                cover=cover,
            )
            if created:
                article.created_time = created
                article.save(
                    update_fields=["created_time"],
                    post_when_save=False,
                    index_when_save=False,
                )
            return "imported"
        except Exception:
            logger.exception("Error importing WordPress post")
            return "failed"

    def run(self) -> None:
        tree = etree.parse(self.metadata["file"], _parser())
        channel = tree.getroot().find("./channel")
        items = channel.findall("item") if channel is not None else []

        # First pass: attachment URLs by wp:post_id. Attachments may appear
        # anywhere in the file, including after the posts referencing them.
        attachments: dict[str, str] = {}
        posts: list[Any] = []
        for item in items:
            post_type = _text(item, _qname("wp", "post_type"))
            if post_type == "attachment":
                post_id = _text(item, _qname("wp", "post_id"))
                url = _text(item, _qname("wp", "attachment_url"))
                if post_id and url:
                    attachments[post_id] = url
            elif post_type == _IMPORTED_POST_TYPE:
                posts.append(item)

        # attachments are consumed, not imported, so they are not counted
        self.metadata["total"] = len(posts)
        self.message = f"found {len(posts)} posts to import"
        self.save(update_fields=["metadata", "message"])

        for item in posts:
            self.progress(self.import_post(item, attachments))

        self.message = (
            f"{self.metadata['imported']} imported, "
            f"{self.metadata['skipped']} skipped, "
            f"{self.metadata['failed']} failed."
        )
        self.save(update_fields=["message"])
