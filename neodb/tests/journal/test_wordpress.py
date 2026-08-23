from io import BytesIO

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils.dateparse import parse_datetime
from lxml import etree
from PIL import Image

from catalog.common.downloaders import BasicImageDownloader
from journal.exporters import WordpressExporter
from journal.exporters.wordpress import _NS
from journal.importers import WordpressImporter
from journal.models import Article
from users.models import User


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(buf, format="PNG")
    return buf.getvalue()


def _wxr(items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<rss version="2.0"'
        ' xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"'
        ' xmlns:content="http://purl.org/rss/1.0/modules/content/"'
        ' xmlns:wfw="http://wellformedweb.org/CommentAPI/"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:wp="http://wordpress.org/export/1.2/">\n'
        "<channel>\n"
        "<title>A Blog</title>\n"
        "<link>https://blog.example.org</link>\n"
        "<wp:wxr_version>1.2</wp:wxr_version>\n"
        "<wp:base_site_url>https://blog.example.org</wp:base_site_url>\n"
        f"{items}\n"
        "</channel>\n"
        "</rss>\n"
    )


def _post(
    post_id: str = "1",
    title: str = "Hello World",
    body: str = "<p>Some <strong>bold</strong> text.</p>",
    status: str = "publish",
    post_type: str = "post",
    date: str = "2021-05-04 09:08:07",
    excerpt: str = "",
    password: str = "",
    tags: str = "",
    postmeta: str = "",
) -> str:
    return f"""<item>
<title>{title}</title>
<pubDate>Tue, 04 May 2021 09:08:07 +0000</pubDate>
<content:encoded><![CDATA[{body}]]></content:encoded>
<excerpt:encoded><![CDATA[{excerpt}]]></excerpt:encoded>
<wp:post_id>{post_id}</wp:post_id>
<wp:post_date>{date}</wp:post_date>
<wp:post_date_gmt>{date}</wp:post_date_gmt>
<wp:status>{status}</wp:status>
<wp:post_type>{post_type}</wp:post_type>
<wp:post_password>{password}</wp:post_password>
<wp:post_parent>0</wp:post_parent>
{tags}
{postmeta}
</item>"""


def _attachment(post_id: str, url: str, parent: str = "1") -> str:
    return f"""<item>
<title>cover.png</title>
<wp:post_id>{post_id}</wp:post_id>
<wp:post_type>attachment</wp:post_type>
<wp:status>inherit</wp:status>
<wp:post_parent>{parent}</wp:post_parent>
<wp:attachment_url>{url}</wp:attachment_url>
</item>"""


def _thumbnail_meta(attachment_id: str) -> str:
    return (
        "<wp:postmeta><wp:meta_key>_thumbnail_id</wp:meta_key>"
        f"<wp:meta_value>{attachment_id}</wp:meta_value></wp:postmeta>"
    )


def _find(item, prefix: str, tag: str):
    return item.find(f"{{{_NS[prefix]}}}{tag}")


def _text(item, prefix: str, tag: str) -> str:
    found = _find(item, prefix, tag)
    return (found.text or "") if found is not None else ""


@pytest.mark.django_db(databases="__all__")
class TestWordpressExport:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="wp_export@test.com", username="wp_exporter")

    def _export(self):
        exporter = WordpressExporter.create(user=self.user)
        exporter.run()
        return etree.parse(exporter.metadata["file"]), exporter

    def test_export_articles(self):
        Article.update_local_article(
            owner=self.user.identity,
            title="Public Read",
            body="**Bold** opening.\n\nSecond paragraph.",
            summary="An essay",
            visibility=0,
            tags=["essays", "longform"],
        )
        Article.update_local_article(
            owner=self.user.identity,
            title="Restricted Read",
            body="only for followers",
            visibility=1,
        )
        tree, exporter = self._export()
        assert exporter.metadata["total"] == 2

        channel = tree.getroot().find("./channel")
        assert _text(channel, "wp", "wxr_version") == "1.2"
        assert _find(channel, "wp", "author") is not None

        items = {i.findtext("title"): i for i in channel.findall("item")}
        assert set(items) == {"Public Read", "Restricted Read"}

        public = items["Public Read"]
        assert _text(public, "wp", "post_type") == "post"
        assert _text(public, "wp", "status") == "publish"
        assert _text(public, "excerpt", "encoded") == "An essay"
        body = _text(public, "content", "encoded")
        assert "<strong>Bold</strong>" in body
        assert "Second paragraph" in body
        assert {c.text for c in public.findall("category")} == {"essays", "longform"}
        assert all(c.get("domain") == "post_tag" for c in public.findall("category"))
        # nothing public becomes private and vice versa
        assert _text(items["Restricted Read"], "wp", "status") == "private"

    def test_export_dates_use_created_time(self):
        article = Article.update_local_article(
            owner=self.user.identity, title="Dated", body="x", visibility=0
        )
        article.created_time = parse_datetime("2021-05-04T09:08:07Z")
        article.save(update_fields=["created_time"], post_when_save=False)
        tree, _ = self._export()
        item = tree.getroot().find("./channel/item")
        assert _text(item, "wp", "post_date_gmt") == "2021-05-04 09:08:07"
        assert "04 May 2021" in (item.findtext("pubDate") or "")

    def test_export_absolutizes_body_images(self):
        src = f"{settings.MEDIA_URL}upload/pic.png"
        Article.update_local_article(
            owner=self.user.identity,
            title="With Image",
            body=f"![pic]({src})",
            visibility=0,
        )
        tree, _ = self._export()
        item = tree.getroot().find("./channel/item")
        body = _text(item, "content", "encoded")
        # a root-relative src would resolve against the *importing* site
        assert f'src="{settings.SITE_INFO["site_url"].rstrip("/")}{src}"' in body

    def test_export_cover_as_attachment(self, tmp_path):
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            Article.update_local_article(
                owner=self.user.identity,
                title="Covered",
                body="body",
                visibility=0,
                cover=SimpleUploadedFile("cover.png", _png(), "image/png"),
            )
            tree, _ = self._export()
            items = tree.getroot().findall("./channel/item")
            posts = [i for i in items if _text(i, "wp", "post_type") == "post"]
            attachments = [
                i for i in items if _text(i, "wp", "post_type") == "attachment"
            ]
            assert len(posts) == 1 and len(attachments) == 1

            attachment = attachments[0]
            post_id = _text(posts[0], "wp", "post_id")
            assert _text(attachment, "wp", "post_parent") == post_id
            assert _text(attachment, "wp", "status") == "inherit"
            assert _text(attachment, "wp", "attachment_url").startswith("http")
            # the post points at the attachment through _thumbnail_id
            metas = {
                _text(m, "wp", "meta_key"): _text(m, "wp", "meta_value")
                for m in posts[0].findall(f"{{{_NS['wp']}}}postmeta")
            }
            assert metas["_thumbnail_id"] == _text(attachment, "wp", "post_id")
            assert metas["_thumbnail_id"] != post_id

    def test_export_without_cover_has_no_attachment(self):
        Article.update_local_article(
            owner=self.user.identity, title="Bare", body="body", visibility=0
        )
        tree, _ = self._export()
        items = tree.getroot().findall("./channel/item")
        assert [_text(i, "wp", "post_type") for i in items] == ["post"]


@pytest.mark.django_db(databases="__all__")
class TestWordpressImport:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="wp_import@test.com", username="wp_importer")

    def _run(self, xml: str, tmp_path, visibility: int = 0) -> WordpressImporter:
        path = tmp_path / "export.xml"
        path.write_text(xml)
        task = WordpressImporter.create(
            self.user, visibility=visibility, file=str(path)
        )
        task.run()
        return task

    def test_import_post(self, tmp_path):
        xml = _wxr(
            _post(
                excerpt="a teaser",
                tags=(
                    '<category domain="post_tag" nicename="tech"><![CDATA[Tech]]>'
                    "</category>"
                    '<category domain="category" nicename="notes"><![CDATA[Notes]]>'
                    "</category>"
                ),
            )
        )
        task = self._run(xml, tmp_path)
        assert task.metadata["imported"] == 1
        article = Article.objects.get(owner=self.user.identity)
        assert article.title == "Hello World"
        assert "**bold**" in article.body
        assert article.summary == "a teaser"
        assert article.visibility == 0
        assert set(article.tags) == {"Tech", "Notes"}
        assert article.created_time == parse_datetime("2021-05-04T09:08:07Z")

    def test_import_respects_chosen_visibility(self, tmp_path):
        task = self._run(_wxr(_post()), tmp_path, visibility=1)
        assert task.metadata["imported"] == 1
        assert Article.objects.get(owner=self.user.identity).visibility == 1

    @pytest.mark.parametrize("status", ["draft", "pending", "private"])
    def test_unpublished_posts_are_never_public(self, tmp_path, status):
        task = self._run(_wxr(_post(status=status)), tmp_path, visibility=0)
        assert task.metadata["imported"] == 1
        assert Article.objects.get(owner=self.user.identity).visibility == 2

    def test_password_protected_post_is_not_public(self, tmp_path):
        self._run(_wxr(_post(password="s3cret")), tmp_path, visibility=0)
        assert Article.objects.get(owner=self.user.identity).visibility == 2

    @pytest.mark.parametrize("status", ["trash", "auto-draft"])
    def test_discarded_statuses_skipped(self, tmp_path, status):
        task = self._run(_wxr(_post(status=status)), tmp_path)
        assert task.metadata["skipped"] == 1
        assert not Article.objects.filter(owner=self.user.identity).exists()

    def test_non_post_types_ignored(self, tmp_path):
        items = "\n".join(
            [
                _post(post_id="1", title="Real Post"),
                _post(post_id="2", title="A Page", post_type="page"),
                _post(post_id="3", title="Menu", post_type="nav_menu_item"),
            ]
        )
        task = self._run(_wxr(items), tmp_path)
        # pages and menu items are not even counted: they are not articles
        assert task.metadata["total"] == 1
        assert task.metadata["imported"] == 1
        titles = list(
            Article.objects.filter(owner=self.user.identity).values_list(
                "title", flat=True
            )
        )
        assert titles == ["Real Post"]

    def test_gutenberg_block_comments_stripped(self, tmp_path):
        body = (
            "<!-- wp:paragraph --><p>Real text.</p><!-- /wp:paragraph -->"
            "<!-- wp:image {&quot;id&quot;:7} --><p>More.</p><!-- /wp:image -->"
        )
        self._run(_wxr(_post(body=body)), tmp_path)
        article = Article.objects.get(owner=self.user.identity)
        assert "wp:paragraph" not in article.body
        assert "Real text." in article.body
        assert "More." in article.body

    def test_import_is_idempotent(self, tmp_path):
        xml = _wxr(_post())
        self._run(xml, tmp_path)
        task = self._run(xml, tmp_path)
        assert task.metadata["skipped"] == 1
        assert task.metadata["imported"] == 0
        assert Article.objects.filter(owner=self.user.identity).count() == 1

    def test_import_featured_image(self, tmp_path, monkeypatch):
        calls = []

        def fake_download(url, page_url, headers=None):
            calls.append(url)
            return _png(), "png"

        monkeypatch.setattr(
            BasicImageDownloader, "download_image", staticmethod(fake_download)
        )
        url = "https://blog.example.org/wp-content/cover.png"
        items = "\n".join(
            [
                _post(postmeta=_thumbnail_meta("9")),
                _attachment("9", url),
            ]
        )
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            task = self._run(items and _wxr(items), tmp_path)
        assert task.metadata["imported"] == 1
        assert calls == [url]
        article = Article.objects.get(owner=self.user.identity)
        assert article.cover and str(article.cover) != settings.DEFAULT_ITEM_COVER

    def test_attachment_without_thumbnail_meta_is_not_a_cover(
        self, tmp_path, monkeypatch
    ):
        """An upload parented to the post is just an upload; only
        _thumbnail_id marks the featured image."""
        monkeypatch.setattr(
            BasicImageDownloader,
            "download_image",
            staticmethod(lambda *a, **k: (_png(), "png")),
        )
        items = "\n".join(
            [_post(), _attachment("9", "https://blog.example.org/inline.png")]
        )
        self._run(_wxr(items), tmp_path)
        article = Article.objects.get(owner=self.user.identity)
        assert str(article.cover) == settings.DEFAULT_ITEM_COVER

    def test_unfetchable_cover_still_imports_article(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            BasicImageDownloader,
            "download_image",
            staticmethod(lambda *a, **k: (None, None)),
        )
        items = "\n".join(
            [
                _post(postmeta=_thumbnail_meta("9")),
                _attachment("9", "http://127.0.0.1/blocked.png"),
            ]
        )
        task = self._run(_wxr(items), tmp_path)
        assert task.metadata["imported"] == 1
        assert task.metadata["failed"] == 0
        article = Article.objects.get(owner=self.user.identity)
        assert str(article.cover) == settings.DEFAULT_ITEM_COVER

    def test_cover_not_fetched_for_skipped_duplicate(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            BasicImageDownloader,
            "download_image",
            staticmethod(lambda url, *a, **k: (calls.append(url), (_png(), "png"))[1]),
        )
        items = "\n".join(
            [
                _post(postmeta=_thumbnail_meta("9")),
                _attachment("9", "https://blog.example.org/cover.png"),
            ]
        )
        xml = _wxr(items)
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            self._run(xml, tmp_path)
            assert len(calls) == 1
            self._run(xml, tmp_path)
        assert len(calls) == 1


@pytest.mark.django_db(databases="__all__")
class TestWordpressValidateFile:
    def test_accepts_wxr(self):
        f = SimpleUploadedFile("e.xml", _wxr(_post()).encode(), "text/xml")
        assert WordpressImporter.validate_file(f) is True
        # the view reads the file after validating, so it must be rewound
        assert f.read(5) == b"<?xml"

    def test_rejects_other_xml(self):
        f = SimpleUploadedFile("e.xml", b"<opml><body/></opml>", "text/xml")
        assert WordpressImporter.validate_file(f) is False

    def test_rejects_junk(self):
        assert (
            WordpressImporter.validate_file(
                SimpleUploadedFile("e.xml", b"not xml at all", "text/xml")
            )
            is False
        )

    def test_rejects_missing_file(self):
        assert WordpressImporter.validate_file(None) is False

    def test_rejects_entity_expansion(self):
        """A billion-laughs payload must not be expanded."""
        bomb = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE rss [<!ENTITY a 'aaaaaaaaaa'>"
            "<!ENTITY b '&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;'>"
            "<!ENTITY c '&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;'>]>"
            "<rss><channel><title>&c;</title></channel></rss>"
        )
        f = SimpleUploadedFile("e.xml", bomb.encode(), "text/xml")
        # no wp: content, so it is rejected either way; the point is that
        # parsing it does not blow up memory first
        assert WordpressImporter.validate_file(f) is False


@pytest.mark.django_db(databases="__all__")
class TestWordpressRoundTrip:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user1 = User.register(email="wp_rt1@test.com", username="wp_rt_out")
        self.user2 = User.register(email="wp_rt2@test.com", username="wp_rt_in")

    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            BasicImageDownloader,
            "download_image",
            staticmethod(lambda *a, **k: (_png(), "png")),
        )
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            article = Article.update_local_article(
                owner=self.user1.identity,
                title="Round Trip",
                body="**Bold** and _italic_.\n\nSecond paragraph.",
                summary="the teaser",
                visibility=0,
                tags=["alpha", "beta"],
                cover=SimpleUploadedFile("cover.png", _png(), "image/png"),
            )
            article.created_time = parse_datetime("2021-05-04T09:08:07Z")
            article.save(update_fields=["created_time"], post_when_save=False)

            exporter = WordpressExporter.create(user=self.user1)
            exporter.run()

            importer = WordpressImporter.create(
                self.user2, visibility=0, file=exporter.metadata["file"]
            )
            importer.run()

        assert importer.metadata["imported"] == 1
        imported = Article.objects.get(owner=self.user2.identity)
        assert imported.title == "Round Trip"
        assert imported.summary == "the teaser"
        assert imported.visibility == 0
        assert set(imported.tags) == {"alpha", "beta"}
        assert imported.created_time == article.created_time
        # markdown survives the HTML detour
        assert "**Bold**" in imported.body
        assert "Second paragraph." in imported.body
        assert str(imported.cover) != settings.DEFAULT_ITEM_COVER
