import datetime
import os
import re
from email.utils import format_datetime

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from lxml import etree

from common.models import SiteConfig
from common.utils import GenerateDateUUIDMediaFilePath
from journal.models import Article
from users.models import Task

# WordPress eXtended RSS. 1.2 is what WordPress itself still emits and what
# every importer (WordPress, Ghost, Substack, Blogger) accepts.
WXR_VERSION = "1.2"

_NS = {
    "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wfw": "http://wellformedweb.org/CommentAPI/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "wp": "http://wordpress.org/export/1.2/",
}

# Root-relative src/href in the rendered body. The archive is consumed by
# another site, where "/media/foo.jpg" resolves against *their* domain, so
# these have to be absolutized on the way out. "//host/x" (protocol-relative)
# is already absolute and must not be touched.
_RE_ROOT_RELATIVE = re.compile(r'\b(src|href)="(/(?!/)[^"]*)"')


def _qname(prefix: str, tag: str) -> str:
    return f"{{{_NS[prefix]}}}{tag}"


def _absolutize(html: str) -> str:
    site_url = settings.SITE_INFO["site_url"].rstrip("/")
    return _RE_ROOT_RELATIVE.sub(
        lambda m: f'{m.group(1)}="{site_url}{m.group(2)}"', html
    )


def _sub(parent, tag: str, text: str | None = None, **attrib: str):
    el = etree.SubElement(parent, tag, attrib=attrib or None)
    if text is not None:
        _set_cdata(el, text)
    return el


def _wp_date(dt: datetime.datetime) -> str:
    """WordPress writes post_date and post_date_gmt as naive
    'YYYY-MM-DD HH:MM:SS'."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _set_cdata(el, text: str) -> None:
    """Put text in a CDATA section, which is how WordPress writes every
    free-text field. lxml refuses to build a CDATA section containing the
    terminator, so fall back to ordinary escaping there -- both forms parse
    to the same string."""
    if "]]>" in text:
        el.text = text
    else:
        el.text = etree.CDATA(text)


class WordpressExporter(Task):
    """Export standalone Articles as a WordPress eXtended RSS (WXR) file.

    Only Articles are exported: marks, reviews and notes are item-linked
    journal entries with no WordPress counterpart (use the CSV/NDJSON
    exporters for those). The output is a single .xml file, which is what
    WordPress's importer consumes directly.
    """

    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "export"
    DefaultMetadata = {
        "file": None,
        "total": 0,
    }

    @property
    def filename(self) -> str:
        d = self.created_time.strftime("%Y%m%d%H%M%S")
        return f"neodb_{self.user.username}_{d}_wordpress"

    def _author_login(self) -> str:
        return self.user.username or str(self.user.pk)

    def _add_author(self, channel) -> None:
        author = etree.SubElement(channel, _qname("wp", "author"))
        _sub(author, _qname("wp", "author_id")).text = "1"
        _sub(author, _qname("wp", "author_login"), self._author_login())
        _sub(author, _qname("wp", "author_email"), self.user.email or "")
        _sub(
            author,
            _qname("wp", "author_display_name"),
            self.user.display_name or self._author_login(),
        )
        _sub(author, _qname("wp", "author_first_name"), "")
        _sub(author, _qname("wp", "author_last_name"), "")

    def _add_attachment(
        self, channel, article: Article, post_id: int, parent_id: int
    ) -> bool:
        """Emit the featured image as an attachment item.

        Only the URL travels: WordPress's importer sideloads the file from
        ``wp:attachment_url`` itself, and the article item points at this
        item through its ``_thumbnail_id`` postmeta.
        """
        url = article.cover_image_url
        if not url:
            return False
        item = etree.SubElement(channel, "item")
        _sub(item, "title", os.path.basename(url) or article.title)
        _sub(item, "link", url)
        _sub(item, "pubDate").text = format_datetime(article.created_time)
        _sub(item, _qname("dc", "creator"), self._author_login())
        _sub(item, "guid", url, isPermaLink="false")
        _sub(item, "description", "")
        _sub(item, _qname("content", "encoded"), "")
        _sub(item, _qname("excerpt", "encoded"), "")
        _sub(item, _qname("wp", "post_id")).text = str(post_id)
        self._add_dates(item, article)
        _sub(item, _qname("wp", "comment_status"), "closed")
        _sub(item, _qname("wp", "ping_status"), "closed")
        _sub(item, _qname("wp", "post_name"), slugify(os.path.basename(url)) or "cover")
        _sub(item, _qname("wp", "status"), "inherit")
        _sub(item, _qname("wp", "post_parent")).text = str(parent_id)
        _sub(item, _qname("wp", "menu_order")).text = "0"
        _sub(item, _qname("wp", "post_type"), "attachment")
        _sub(item, _qname("wp", "post_password"), "")
        _sub(item, _qname("wp", "is_sticky")).text = "0"
        _sub(item, _qname("wp", "attachment_url"), url)
        return True

    def _add_dates(self, item, article: Article) -> None:
        created = article.created_time
        edited = article.edited_time or created
        _sub(item, _qname("wp", "post_date"), _wp_date(timezone.localtime(created)))
        _sub(
            item,
            _qname("wp", "post_date_gmt"),
            _wp_date(created.astimezone(datetime.UTC)),
        )
        _sub(item, _qname("wp", "post_modified"), _wp_date(timezone.localtime(edited)))
        _sub(
            item,
            _qname("wp", "post_modified_gmt"),
            _wp_date(edited.astimezone(datetime.UTC)),
        )

    def _add_article(self, channel, article: Article, post_id: int):
        item = etree.SubElement(channel, "item")
        _sub(item, "title", article.title)
        _sub(item, "link", article.absolute_url)
        _sub(item, "pubDate").text = format_datetime(article.created_time)
        _sub(item, _qname("dc", "creator"), self._author_login())
        _sub(item, "guid", article.absolute_url, isPermaLink="false")
        _sub(item, "description", "")
        _sub(item, _qname("content", "encoded"), _absolutize(article.html_content))
        _sub(item, _qname("excerpt", "encoded"), article.summary or "")
        _sub(item, _qname("wp", "post_id")).text = str(post_id)
        self._add_dates(item, article)
        _sub(item, _qname("wp", "comment_status"), "closed")
        _sub(item, _qname("wp", "ping_status"), "closed")
        _sub(
            item,
            _qname("wp", "post_name"),
            slugify(article.title) or str(article.uuid),
        )
        # Anything not fully public becomes a WordPress private post; there is
        # no WordPress equivalent of followers-only or mentioned-only.
        _sub(
            item,
            _qname("wp", "status"),
            "publish" if article.visibility == 0 else "private",
        )
        _sub(item, _qname("wp", "post_parent")).text = "0"
        _sub(item, _qname("wp", "menu_order")).text = "0"
        _sub(item, _qname("wp", "post_type"), "post")
        _sub(item, _qname("wp", "post_password"), "")
        _sub(item, _qname("wp", "is_sticky")).text = "0"
        for tag in article.normalized_tags:
            _sub(item, "category", tag, domain="post_tag", nicename=slugify(tag) or tag)
        return item

    def _add_thumbnail_meta(self, item, attachment_id: int) -> None:
        meta = etree.SubElement(item, _qname("wp", "postmeta"))
        _sub(meta, _qname("wp", "meta_key"), "_thumbnail_id")
        _sub(meta, _qname("wp", "meta_value"), str(attachment_id))

    def run(self) -> None:
        site_url = settings.SITE_INFO["site_url"].rstrip("/")
        root = etree.Element("rss", nsmap=_NS, attrib={"version": "2.0"})
        channel = etree.SubElement(root, "channel")
        _sub(channel, "title").text = SiteConfig.system.site_name
        _sub(channel, "link").text = site_url
        _sub(channel, "description").text = self.user.display_name or ""
        _sub(channel, "pubDate").text = format_datetime(timezone.now())
        _sub(channel, "language").text = "en-US"
        _sub(channel, _qname("wp", "wxr_version")).text = WXR_VERSION
        _sub(channel, _qname("wp", "base_site_url")).text = site_url
        _sub(channel, _qname("wp", "base_blog_url")).text = site_url
        self._add_author(channel)

        total = 0
        # a single counter across posts and attachments: wp:post_id has to be
        # unique within the file, and the importer keys _thumbnail_id off it
        next_id = 1
        for article in Article.objects.filter(owner=self.user.identity).order_by(
            "created_time"
        ):
            post_id = next_id
            next_id += 1
            item = self._add_article(channel, article, post_id)
            attachment_id = next_id
            if self._add_attachment(channel, article, attachment_id, post_id):
                next_id += 1
                self._add_thumbnail_meta(item, attachment_id)
            total += 1

        filename = GenerateDateUUIDMediaFilePath(
            "f.xml", settings.MEDIA_ROOT + "/" + settings.EXPORT_FILE_PATH_ROOT
        )
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        etree.ElementTree(root).write(
            filename, xml_declaration=True, encoding="UTF-8", pretty_print=True
        )
        self.metadata["file"] = filename
        self.metadata["total"] = total
        self.message = f"{total} articles exported."
        self.save()
