"""Tests for the user upload registry (``journal.models.Attachment``)."""

import io
from unittest import mock
from urllib.parse import urlparse

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalog.models import Edition
from journal.jobs.migrations import backfill_attachments_20260818
from journal.models import Article, Attachment, Collection, Note, Review
from journal.models.attachment import takahe_attachment_urls, takahe_media_path
from journal.models.utils import remove_data_by_identity
from takahe.models import Post, PostAttachment
from takahe.utils import Takahe
from users.models import User


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, format="PNG")
    return buf.getvalue()


def _stored(identity_id: int, name: str = "x.png") -> str:
    """Put a file where an upload would live and return its storage path."""
    return default_storage.save(
        f"upload/{identity_id}/2026/{name}", ContentFile(_png_bytes())
    )


def _name(f) -> str:
    """Storage name of a FieldFile as a plain str."""
    return str(f.name or "")


def _save_quiet(piece):
    """Save without firing the federation/index side effects."""
    piece.save(post_when_save=False, index_when_save=False)
    return piece


def _api_token(user: User) -> str:
    app = Takahe.get_or_create_app(
        "Attachment API Tests",
        "https://example.org",
        "https://example.org/callback",
        owner_pk=user.identity.pk,
    )
    return Takahe.refresh_token(app, user.identity.pk, user.pk)


@pytest.mark.django_db(databases="__all__")
class TestAttachmentModel:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="atta@test.com", username="atta_user")
        self.identity = self.user.identity

    def test_register_stores_file_under_upload_path(self):
        a = Attachment.register(
            self.identity, ContentFile(_png_bytes()), "png", mimetype="image/png"
        )
        assert _name(a.file).startswith(f"upload/{self.identity.pk}/")
        assert _name(a.file).endswith(".png")
        assert a.mimetype == "image/png"
        assert a.size > 0
        assert a.type == "image"
        assert a.url == a.file.url
        # no thumbnail: the preview falls back to the full-size file, so a
        # note card never renders an empty src
        assert a.preview_url == a.url

    def test_adopt_is_idempotent_and_skips_missing_files(self):
        path = _stored(self.identity.pk)
        first = Attachment.adopt(self.identity, path)
        second = Attachment.adopt(self.identity, path)
        assert first is not None
        assert second is not None
        assert first.pk == second.pk
        assert Attachment.objects.filter(file=path).count() == 1
        assert Attachment.adopt(self.identity, "upload/nope/2026/gone.png") is None

    def test_duration_survives_round_trip(self):
        a = Attachment.register(
            self.identity,
            ContentFile(b"not really a video"),
            "mp4",
            mimetype="video/mp4",
            duration=12.5,
        )
        a.refresh_from_db()
        assert a.duration == 12.5
        assert a.type == "video"

    def test_remote_row_keeps_both_urls(self):
        a = Attachment.objects.create(
            owner=self.identity,
            remote_url="https://remote.example/full.png",
            remote_preview_url="https://remote.example/thumb.png",
            mimetype="image/png",
        )
        assert a.url == "https://remote.example/full.png"
        assert a.preview_url == "https://remote.example/thumb.png"
        assert a.to_json() == {
            "type": "image",
            "mimetype": "image/png",
            "url": "https://remote.example/full.png",
            "preview_url": "https://remote.example/thumb.png",
        }

    def test_deleting_an_attachment_resets_covers_naming_it(self):
        """A cover is just a stored path, so it can name an attachment's file.
        Deleting the bytes must not leave a piece rendering a missing image."""
        from catalog.models import CatalogCollection

        from journal.models import Collection

        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        name = _name(a.file)
        article = Article.update_local_article(
            owner=self.identity, title="Cover", body="b"
        )
        Article.objects.filter(pk=article.pk).update(cover=name)
        collection = _save_quiet(Collection(owner=self.identity, title="C", brief="b"))
        Collection.objects.filter(pk=collection.pk).update(cover=name)
        catalog_item = collection.catalog_item
        assert catalog_item is not None
        CatalogCollection.objects.filter(pk=catalog_item.pk).update(cover=name)

        a.delete_files()

        article.refresh_from_db()
        collection.refresh_from_db()
        catalog_item.refresh_from_db()
        assert str(article.cover) == settings.DEFAULT_ITEM_COVER
        assert str(collection.cover) == settings.DEFAULT_ITEM_COVER
        assert str(catalog_item.cover) == settings.DEFAULT_ITEM_COVER
        assert not default_storage.exists(name)

    def test_deleting_the_row_reclaims_the_bytes(self):
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        name = _name(a.file)
        assert default_storage.exists(name)
        a.delete()
        assert not default_storage.exists(name)
        assert not Attachment.objects.filter(pk=a.pk).exists()

    def test_delete_refuses_a_file_outside_the_owners_path(self):
        """Deletion is the irreversible half, so it re-checks ownership rather
        than trusting a row that names someone else's path."""
        foreign = default_storage.save("item/other.png", ContentFile(_png_bytes()))
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        mine = _name(a.file)
        # a row naming a path the owner could not have written
        Attachment.objects.filter(pk=a.pk).update(file=foreign)
        a.refresh_from_db()

        a.delete_files()

        assert default_storage.exists(foreign)  # left alone
        default_storage.delete(foreign)
        default_storage.delete(mine)

    def test_many_pointer_rows_coexist(self):
        """`file` is unique, and pointer rows (remote media, never downloaded)
        leave it unset. If Django stored that as '' rather than NULL the second
        such row anywhere would raise, aborting note sync and the backfill."""
        rows = [
            Attachment.objects.create(
                owner=self.identity,
                remote_url=f"https://remote.example/{i}.png",
                mimetype="image/png",
            )
            for i in range(3)
        ]
        assert len({r.pk for r in rows}) == 3
        # stored as '' rather than NULL, which is why a table-wide unique index
        # on `file` collided instead of being ignored
        assert Attachment.objects.filter(file="").count() == 3

    def test_delete_files_removes_blobs(self):
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        name = _name(a.file)
        assert default_storage.exists(name)
        a.delete_files()
        assert not default_storage.exists(name)


class TestTakaheMediaPath:
    def test_resolves_local_takahe_mount(self):
        url = settings.TAKAHE_MEDIA_URL + "attachments/2026/1/2/abc.png"
        assert takahe_media_path(url) == "attachments/2026/1/2/abc.png"

    def test_resolves_shared_media_mount_as_on_s3(self):
        # on S3 both stores share one bucket and base URL, so the takahe key
        # prefix is the only thing that identifies the file
        url = settings.MEDIA_URL + "attachment_thumbnails/2026/1/2/abc.png"
        assert takahe_media_path(url) == "attachment_thumbnails/2026/1/2/abc.png"

    def test_resolves_absolute_url_on_our_own_host(self):
        # the NDJSON importer absolutizes note attachment URLs against the site
        host = settings.SITE_DOMAINS[0]
        url = f"https://{host}" + settings.MEDIA_URL + "attachments/a/b.png"
        assert takahe_media_path(url) == "attachments/a/b.png"

    def test_rejects_non_takahe_media(self):
        assert takahe_media_path(settings.MEDIA_URL + "upload/1/2026/x.png") is None
        assert takahe_media_path("https://elsewhere.example/x.png") is None
        assert takahe_media_path("") is None

    def test_resolves_the_host_our_own_writer_uses(self):
        """takahe_attachment_urls absolutizes against site_url, so the resolver
        has to accept that host or it would reject its own output."""
        # build the path portion from whichever form TAKAHE_MEDIA_URL takes;
        # it is absolute under compose and relative by default, and its host
        # need not match site_url's
        host = urlparse(settings.SITE_INFO["site_url"]).hostname
        prefix = settings.TAKAHE_MEDIA_URL
        prefix_path = urlparse(prefix).path if "://" in prefix else prefix
        url = f"https://{host}{prefix_path}attachments/a/b.png"
        assert takahe_media_path(url) == "attachments/a/b.png"

    def test_rejects_a_foreign_host_mimicking_our_media_path(self):
        """The URL on a federated attachment is remote-controlled, so a path
        that looks like ours must not be trusted from someone else's host."""
        crafted = (
            "https://evil.example" + settings.MEDIA_URL + "attachments/2026/1/2/x.png"
        )
        assert takahe_media_path(crafted) is None


class TestTakaheAttachmentUrls:
    """``PostAttachment.full_url()`` wraps its value in ``RelativeAbsoluteUrl``,
    which raises on a schemeless URL -- and takahe serves schemeless URLs
    whenever ``TAKAHE_MEDIA_URL`` is relative, the settings default. Reading
    ``.absolute`` there aborts note ingestion, so we resolve the URLs
    ourselves and must never raise."""

    # Real (unsaved) PostAttachments rather than stubs, so the production
    # signature stays honest and the storage plumbing is the real one.

    def test_relative_urls_become_absolute(self):
        atta = PostAttachment(
            pk=7,
            mimetype="image/png",
            file="attachments/a.png",
            thumbnail="attachment_thumbnails/a.png",
        )
        with mock.patch.object(storages["takahe"], "base_url", "/media/"):
            full, preview = takahe_attachment_urls(atta)
        assert full.startswith("http")
        assert full.endswith("/media/attachments/a.png")
        assert preview.endswith("/media/attachment_thumbnails/a.png")

    def test_absolute_urls_pass_through(self):
        atta = PostAttachment(pk=7, mimetype="image/png", file="attachments/a.png")
        with mock.patch.object(storages["takahe"], "base_url", "https://cdn.example/"):
            full, preview = takahe_attachment_urls(atta)
        assert full == "https://cdn.example/attachments/a.png"
        # no thumbnail: preview falls back to the full file
        assert preview == full

    def test_uncached_remote_image_uses_the_proxy(self):
        # takahe proxies rather than hotlinking, keeping the viewer's IP away
        # from the origin; preserve that instead of exposing remote_url
        atta = PostAttachment(
            pk=7, mimetype="image/png", remote_url="https://far.example/a.png"
        )
        full, preview = takahe_attachment_urls(atta)
        assert "/proxy/post_attachment/7/" in full
        assert "/proxy/post_attachment/7/" in preview

    def test_non_image_falls_back_to_remote_url(self):
        atta = PostAttachment(
            pk=7, mimetype="audio/mpeg", remote_url="https://far.example/a.mp3"
        )
        full, _ = takahe_attachment_urls(atta)
        assert full == "https://far.example/a.mp3"

    def test_nothing_available_yields_empty_rather_than_raising(self):
        full, preview = takahe_attachment_urls(
            PostAttachment(pk=7, mimetype="audio/mpeg")
        )
        assert full == ""
        assert preview == ""


@pytest.mark.django_db(databases="__all__")
class TestLinkAttachmentsToPiece:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="link@test.com", username="link_user")
        self.identity = self.user.identity

    def test_article_save_links_embedded_upload(self):
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        article = Article.update_local_article(
            owner=self.identity,
            title="With image",
            body=f"![]({a.url})",
        )
        assert list(article.attachment_records.all()) == [a]
        assert list(a.pieces.all()) == [article]

    def test_editing_out_an_image_unlinks_but_keeps_the_row(self):
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        article = Article.update_local_article(
            owner=self.identity, title="T", body=f"![]({a.url})"
        )
        assert article.attachment_records.count() == 1
        Article.update_local_article(
            owner=self.identity, title="T", body="no images now", article=article
        )
        assert article.attachment_records.count() == 0
        # the upload itself survives, so an accidental removal is recoverable
        assert Attachment.objects.filter(pk=a.pk).exists()

    def test_one_upload_shared_by_two_pieces(self):
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        first = Article.update_local_article(
            owner=self.identity, title="One", body=f"![]({a.url})"
        )
        second = Article.update_local_article(
            owner=self.identity, title="Two", body=f"copy ![]({a.url})"
        )
        assert set(a.pieces.all()) == {first, second}
        # removing it from one leaves the other's link intact
        Article.update_local_article(
            owner=self.identity, title="One", body="gone", article=first
        )
        assert list(a.pieces.all()) == [second]

    def test_external_and_unstored_images_are_not_registered(self):
        article = Article.update_local_article(
            owner=self.identity,
            title="External",
            body="![](https://elsewhere.example/x.png)",
        )
        assert article.attachment_records.count() == 0
        assert Attachment.objects.count() == 0

    def test_adopts_a_body_image_that_predates_the_registry(self):
        path = _stored(self.identity.pk)
        article = Article.update_local_article(
            owner=self.identity,
            title="Legacy",
            body=f"see ![alt]({settings.MEDIA_URL}{path})",
        )
        rows = list(article.attachment_records.all())
        assert len(rows) == 1
        assert _name(rows[0].file) == path
        assert rows[0].owner == self.identity

    def test_another_users_image_is_not_claimed(self):
        """Hotlinking someone else's upload must not register it as mine.

        Otherwise deleting my account would delete their file.
        """
        other = User.register(email="other2@test.com", username="other2_user")
        theirs = Attachment.register(other.identity, ContentFile(_png_bytes()), "png")
        article = Article.update_local_article(
            owner=self.identity, title="Hotlink", body=f"![]({theirs.url})"
        )
        assert article.attachment_records.count() == 0
        assert list(theirs.pieces.all()) == []
        assert Attachment.objects.count() == 1  # still only theirs

    def test_traversal_path_cannot_claim_another_users_file(self):
        """`safe_join` only blocks escaping MEDIA_ROOT, so
        `upload/<mine>/../../<yours>` resolves to a real file inside it. A
        lexical owner check would accept it and hand the embedder a row
        pointing at media they do not own -- and with it the delete endpoint
        and the account-deletion sweep."""
        victim = default_storage.save("victim/secret.png", ContentFile(_png_bytes()))
        traversal = f"upload/{self.identity.pk}/../../{victim}"
        # the premise: it really does resolve, so the guard is load-bearing
        assert default_storage.exists(traversal)

        article = Article.update_local_article(
            owner=self.identity,
            title="Traversal",
            body=f"![]({settings.MEDIA_URL}{traversal})",
        )
        assert article.attachment_records.count() == 0
        assert Attachment.objects.count() == 0
        assert Attachment.adopt(self.identity, traversal) is None
        # and the victim's file is untouched
        assert default_storage.exists(victim)
        default_storage.delete(victim)

    def test_review_save_links_embedded_upload(self):
        item = Edition.objects.create(title="Reviewed Book")
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        review = Review.update_item_review(
            item, self.identity, "Title", f"body ![]({a.url})"
        )
        assert review is not None
        assert list(review.attachment_records.all()) == [a]


@pytest.mark.django_db(databases="__all__")
class TestUploadEndpoints:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="up@test.com", username="up_user")
        self.identity = self.user.identity
        self.client = Client()
        self.client.force_login(self.user)

    def test_web_upload_registers_and_keeps_response_shape(self):
        response = self.client.post(
            reverse("journal:upload_image"),
            {"image": SimpleUploadedFile("x.png", _png_bytes(), "image/png")},
        )
        assert response.status_code == 200
        path = response.json()["data"]["filePath"]
        a = Attachment.objects.get(owner=self.identity)
        assert path == a.url
        assert a.pieces.count() == 0  # registered, not yet embedded

    def test_web_upload_rejects_non_image(self):
        response = self.client.post(
            reverse("journal:upload_image"),
            {"image": SimpleUploadedFile("x.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 400
        assert Attachment.objects.count() == 0

    def test_api_upload_lists_and_deletes(self):
        token = _api_token(self.user)
        bearer = f"Bearer {token}"
        client = Client()

        response = client.post(
            "/api/me/attachment/",
            {
                "file": SimpleUploadedFile("x.png", _png_bytes(), "image/png"),
                "description": "alt words",
            },
            HTTP_AUTHORIZATION=bearer,
        )
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["url"]
        assert body["mimetype"] == "image/png"
        assert body["width"] == 2 and body["height"] == 2
        assert body["description"] == "alt words"
        uuid_ = body["uuid"]

        a = Attachment.objects.get(owner=self.identity)
        assert a.uuid == uuid_
        stored = _name(a.file)

        listed = client.get("/api/me/attachment/", HTTP_AUTHORIZATION=bearer)
        assert listed.status_code == 200
        assert [x["uuid"] for x in listed.json()["data"]] == [uuid_]

        deleted = client.delete(
            f"/api/me/attachment/{uuid_}", HTTP_AUTHORIZATION=bearer
        )
        assert deleted.status_code == 200
        assert not Attachment.objects.filter(pk=a.pk).exists()
        assert not default_storage.exists(stored)

    def test_api_upload_rejects_non_image(self):
        token = _api_token(self.user)
        response = Client().post(
            "/api/me/attachment/",
            {"file": SimpleUploadedFile("x.txt", b"hello", "text/plain")},
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 400
        assert Attachment.objects.count() == 0

    def test_api_upload_requires_auth(self):
        response = Client().post(
            "/api/me/attachment/",
            {"file": SimpleUploadedFile("x.png", _png_bytes(), "image/png")},
        )
        assert response.status_code == 401
        assert Attachment.objects.count() == 0

    def test_api_delete_rejects_another_users_attachment(self):
        other = User.register(email="other@test.com", username="other_user")
        a = Attachment.register(other.identity, ContentFile(_png_bytes()), "png")
        token = _api_token(self.user)
        response = Client().delete(
            f"/api/me/attachment/{a.uuid}",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 404
        assert Attachment.objects.filter(pk=a.pk).exists()

    def test_api_delete_tolerates_a_malformed_uuid(self):
        token = _api_token(self.user)
        response = Client().delete(
            "/api/me/attachment/not-a-uuid",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
class TestNoteAttachments:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="note@test.com", username="note_user")
        self.identity = self.user.identity
        self.item = Edition.objects.create(title="A Book")

    def _note(self, attachments=None) -> Note:
        return _save_quiet(
            Note(
                owner=self.identity,
                item=self.item,
                title="t",
                content="c",
                attachments=attachments or [],
            )
        )

    def test_attachment_list_falls_back_to_legacy_json(self):
        legacy = [
            {
                "type": "image",
                "mimetype": "image/png",
                "url": "/media/attachments/2026/1/2/a.png",
                "preview_url": "/media/attachment_thumbnails/2026/1/2/a.png",
            }
        ]
        note = self._note(legacy)
        assert note.attachment_list == legacy

    def test_attachment_list_prefers_registry_rows(self):
        note = self._note([{"type": "image", "mimetype": "", "url": "/legacy.png"}])
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        note.attachment_records.add(a)
        assert note.attachment_list == [a]

    def test_from_legacy_json_copies_takahe_media(self):
        rel = storages["takahe"].save(
            "attachments/2026/1/2/copied.png", ContentFile(_png_bytes())
        )
        entry = {"mimetype": "image/png", "url": settings.TAKAHE_MEDIA_URL + rel}
        a = Attachment.from_legacy_json(self.identity, entry)
        assert a is not None
        assert a.file
        # a real copy into our own storage, not a pointer at takahe's
        assert _name(a.file).startswith(f"upload/{self.identity.pk}/")
        assert default_storage.exists(_name(a.file))
        assert a.size > 0
        # deduped on source, so a second pass does not copy again
        again = Attachment.from_legacy_json(self.identity, entry)
        assert again is not None and again.pk == a.pk

    def test_recovered_copy_upgrades_the_fallback_pointer(self):
        """A transiently failed copy leaves a pointer row. A later rerun must
        upgrade it, not add a second row for the same media -- the backfill
        adds without removing, so the note would render it twice."""
        rel = storages["takahe"].save(
            "attachments/2026/5/5/flaky.png", ContentFile(_png_bytes())
        )
        entry = {"mimetype": "image/png", "url": settings.TAKAHE_MEDIA_URL + rel}

        with mock.patch.object(Attachment, "_copy_into_storage", return_value=None):
            pointer = Attachment.from_legacy_json(self.identity, entry)
        assert pointer is not None
        assert not pointer.file
        assert Attachment.objects.count() == 1

        recovered = Attachment.from_legacy_json(self.identity, entry)
        assert recovered is not None
        assert recovered.pk == pointer.pk
        assert recovered.file
        assert Attachment.objects.count() == 1

    def test_long_urls_sharing_a_prefix_stay_distinct(self):
        """The source key is bounded to fit the column; truncating a 2500-char
        URL would merge two distinct ones and render the wrong media."""
        shared = "https://far.example/" + ("c" * 600)
        first = Attachment.from_legacy_json(
            self.identity, {"url": shared + "/one.png", "mimetype": "image/png"}
        )
        second = Attachment.from_legacy_json(
            self.identity, {"url": shared + "/two.png", "mimetype": "image/png"}
        )
        assert first is not None and second is not None
        assert first.pk != second.pk
        assert first.source != second.source
        assert first.remote_url.endswith("/one.png")
        assert second.remote_url.endswith("/two.png")

    def test_from_legacy_json_keeps_remote_url_as_pointer(self):
        entry = {
            "mimetype": "image/png",
            "url": "https://remote.example/x.png",
            "preview_url": "https://remote.example/t.png",
        }
        a = Attachment.from_legacy_json(self.identity, entry)
        assert a is not None
        assert not a.file
        assert a.remote_url == "https://remote.example/x.png"
        assert a.preview_url == "https://remote.example/t.png"


@pytest.mark.django_db(databases="__all__")
class TestSyncFromPost:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="sync@test.com", username="sync_user")
        self.identity = self.user.identity
        self.item = Edition.objects.create(title="B Book")

    def _note_with_post(self, local: bool) -> tuple[Note, Post]:
        note = _save_quiet(
            Note(owner=self.identity, item=self.item, title="t", content="c")
        )
        post = Post.objects.create(
            author_id=self.identity.pk,
            content="c",
            local=local,
            object_uri=f"https://x.test/{'l' if local else 'r'}/1",
            type="Note",
            visibility=Post.Visibilities.public,
            state="fanned_out",
        )
        return note, post

    def test_local_post_media_is_copied(self):
        note, post = self._note_with_post(local=True)
        atta = PostAttachment.objects.create(
            post=post,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="a.png"),
            width=2,
            height=2,
            name="alt text",
        )
        rows = Attachment.sync_from_post(note, post)
        assert len(rows) == 1
        a = rows[0]
        assert a.file
        assert default_storage.exists(_name(a.file))
        assert a.width == 2 and a.height == 2
        assert a.description == "alt text"
        assert a.source == f"takahe:{atta.pk}"
        assert list(note.attachment_records.all()) == [a]
        # runs on every post fetch, so a second sync must not copy again
        assert Attachment.sync_from_post(note, post) == [a]
        assert Attachment.objects.count() == 1

    def test_removing_an_image_from_the_post_unlinks_it(self):
        """The legacy JSON was rebuilt on every save, so a deleted image
        disappeared. Rows must reconcile or the card shows it forever."""
        note, post = self._note_with_post(local=True)
        kept = PostAttachment.objects.create(
            post=post,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="keep.png"),
        )
        removed = PostAttachment.objects.create(
            post=post,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="drop.png"),
        )
        assert len(Attachment.sync_from_post(note, post)) == 2

        removed.delete()  # author deletes one image via the Mastodon API
        rows = Attachment.sync_from_post(note, post)
        assert len(rows) == 1
        assert rows[0].source == f"takahe:{kept.pk}"
        assert note.attachment_list == rows

    def test_stale_post_does_not_roll_back_media(self):
        """An out-of-order refetch returns the note untouched (the edited_time
        guard in Content.update_by_ap_object). Reconciling against it anyway
        would add the old post's media and prune the current media."""
        note, current = self._note_with_post(local=False)
        # the stale post is unlinked, so the piece is found by its AP id
        note.remote_id = "https://x.test/note/1"
        note.save(post_when_save=False, index_when_save=False)
        current_atta = PostAttachment.objects.create(
            post=current,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="current.png"),
        )
        note.link_post_id(current.pk)
        Attachment.sync_from_post(note, current)
        assert note.attachment_records.count() == 1

        # a stale post arrives while `current` is still the note's latest
        stale = Post.objects.create(
            author_id=self.identity.pk,
            content="old",
            local=False,
            object_uri="https://x.test/stale/1",
            type="Note",
            visibility=Post.Visibilities.public,
            state="fanned_out",
        )
        PostAttachment.objects.create(
            post=stale,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="stale.png"),
        )
        obj = {
            "id": "https://x.test/note/1",
            "content": "old",
            "published": "2020-01-01T00:00:00+00:00",
            "updated": "2020-01-01T00:00:00+00:00",
        }
        Note.update_by_ap_object(self.identity, self.item, obj, stale)

        rows = list(note.attachment_records.all())
        assert len(rows) == 1
        assert rows[0].source == f"takahe:{current_atta.pk}"

    def test_reconcile_keeps_rows_from_other_sources(self):
        """A backfilled copy of an already-pruned post's media has no other
        source left; a post sync must not unlink it."""
        note, post = self._note_with_post(local=True)
        backfilled = Attachment.objects.create(
            owner=self.identity,
            file="upload/x/2026/backfilled.png",
            mimetype="image/png",
            source="takahe-media:attachments/2026/1/1/old.png",
        )
        uploaded = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        note.attachment_records.add(backfilled, uploaded)

        Attachment.sync_from_post(note, post)  # post has no attachments

        assert set(note.attachment_records.all()) == {backfilled, uploaded}

    def test_failed_local_copy_is_retried_not_settled(self):
        """takahe prunes posts, so a transient copy failure must not be
        recorded as done -- that would forfeit the copy permanently."""
        note, post = self._note_with_post(local=True)
        atta = PostAttachment.objects.create(
            post=post,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="flaky.png"),
            remote_url="https://takahe.example/flaky.png",
        )
        with mock.patch.object(Attachment, "_copy_into_storage", return_value=None):
            rows = Attachment.sync_from_post(note, post)
        assert len(rows) == 1
        pending = rows[0]
        assert not pending.file
        assert pending.source == f"takahe-pending:{atta.pk}"

        # a later sync retries and upgrades the same row in place
        rows = Attachment.sync_from_post(note, post)
        assert len(rows) == 1
        assert rows[0].pk == pending.pk
        assert rows[0].file
        assert rows[0].source == f"takahe:{atta.pk}"
        assert Attachment.objects.count() == 1
        assert list(note.attachment_records.all()) == [rows[0]]

    def test_sync_survives_a_relative_takahe_media_url(self):
        """Reproduces CI, where TAKAHE_MEDIA_URL is the relative default.
        compose.yml sets an absolute one, which is why this hid locally --
        pinning it here rather than relying on the ambient config."""
        note, post = self._note_with_post(local=False)
        PostAttachment.objects.create(
            post=post,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="rel.png"),
        )
        with mock.patch.object(storages["takahe"], "base_url", "/media/"):
            rows = Attachment.sync_from_post(note, post)
        assert len(rows) == 1
        # resolved to an absolute URL rather than raising
        assert rows[0].remote_url.startswith("http")
        assert rows[0].remote_url.endswith("/media/attachments/rel.png") or (
            "/media/" in rows[0].remote_url
        )

    def test_legacy_json_survives_a_relative_takahe_media_url(self):
        """The same call existed in params_from_ap_object before this branch,
        so note ingestion itself was already exposed to it."""
        note, post = self._note_with_post(local=False)
        PostAttachment.objects.create(
            post=post,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="legacy.png"),
        )
        with mock.patch.object(storages["takahe"], "base_url", "/media/"):
            params = Note.params_from_ap_object(post, {"content": "x"}, None)
        entries = params["attachments"]
        assert len(entries) == 1
        assert entries[0]["url"].startswith("http")
        assert entries[0]["preview_url"].startswith("http")

    def test_long_remote_url_is_not_truncated(self):
        """takahe stores remote_url as varchar(2500); truncating at 500 would
        persist a broken URL, and attachment_list prefers rows over the intact
        legacy JSON, so the note's media would break."""
        # Asserted on the field rather than round-tripped through a
        # PostAttachment fixture: the takahe *mirror* in neodb/takahe/models.py
        # still declares remote_url as 500, and the test DB is built from the
        # mirror, so a >500 fixture cannot be inserted here even though the
        # real column (activities migration 0020) is 2500.
        for name in ("remote_url", "remote_preview_url"):
            field = Attachment._meta.get_field(name)
            assert getattr(field, "max_length", None) == 2500, name

    def test_long_legacy_url_survives_registration(self):
        long_url = "https://far.example/" + ("b" * 1200) + ".png"
        a = Attachment.from_legacy_json(
            self.identity, {"url": long_url, "mimetype": "image/png"}
        )
        assert a is not None
        a.refresh_from_db()
        assert a.remote_url == long_url

    def test_remote_post_media_is_not_downloaded(self):
        note, post = self._note_with_post(local=False)
        PostAttachment.objects.create(
            post=post,
            author_id=self.identity.pk,
            mimetype="image/png",
            file=ContentFile(_png_bytes(), name="r.png"),
            width=2,
            height=2,
        )
        rows = Attachment.sync_from_post(note, post)
        assert len(rows) == 1
        assert not rows[0].file
        assert rows[0].remote_url


@pytest.mark.django_db(databases="__all__")
class TestBackfillJob:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="bf@test.com", username="bf_user")
        self.identity = self.user.identity
        self.item = Edition.objects.create(title="C Book")

    def test_backfill_registers_bodies_and_note_media(self):
        path = _stored(self.identity.pk, "legacy.png")
        body = f"![]({settings.MEDIA_URL}{path})"
        review = _save_quiet(
            Review(owner=self.identity, item=self.item, title="R", body=body)
        )
        collection = _save_quiet(Collection(owner=self.identity, title="C", brief=body))
        rel = storages["takahe"].save(
            "attachments/2026/3/4/note.png", ContentFile(_png_bytes())
        )
        note = _save_quiet(
            Note(
                owner=self.identity,
                item=self.item,
                title="n",
                content="c",
                attachments=[
                    {
                        "type": "image",
                        "mimetype": "image/png",
                        "url": settings.TAKAHE_MEDIA_URL + rel,
                    }
                ],
            )
        )

        backfill_attachments_20260818()

        # body images adopted in place and shared by both pieces
        adopted = Attachment.objects.get(file=path)
        assert set(adopted.pieces.all()) == {review, collection}
        # note media copied out of takahe into our own storage
        note_rows = list(note.attachment_records.all())
        assert len(note_rows) == 1
        assert note_rows[0].file
        assert _name(note_rows[0].file).startswith(f"upload/{self.identity.pk}/")
        # the legacy JSON is deliberately left untouched
        note.refresh_from_db()
        assert note.attachments[0]["url"].endswith(rel)

    def test_backfill_is_safe_to_rerun_for_bodies(self):
        path = _stored(self.identity.pk, "twice.png")
        _save_quiet(
            Review(
                owner=self.identity,
                item=self.item,
                title="R",
                body=f"![]({settings.MEDIA_URL}{path})",
            )
        )
        backfill_attachments_20260818()
        backfill_attachments_20260818()
        assert Attachment.objects.filter(file=path).count() == 1


@pytest.mark.django_db(databases="__all__")
class TestOrdering:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="ord@test.com", username="ord_user")
        self.identity = self.user.identity
        self.item = Edition.objects.create(title="D Book")

    def test_attachment_list_keeps_creation_order(self):
        """Templates key lightbox anchors off forloop.counter, so the sequence
        has to be stable rather than whatever the DB returns."""
        note = _save_quiet(
            Note(owner=self.identity, item=self.item, title="n", content="c")
        )
        rows = [
            Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
            for _ in range(4)
        ]
        # add in a shuffled order; the read must still come back in creation order
        note.attachment_records.add(rows[2], rows[0], rows[3], rows[1])
        assert note.attachment_list == rows

    def test_prefetch_is_honoured_by_attachment_list(self):
        """Ordering lives in Meta, not in an order_by() at the read site --
        a re-sort would defeat the prefetch and restore the per-card query."""
        for i in range(3):
            note = _save_quiet(
                Note(owner=self.identity, item=self.item, title=f"n{i}", content="c")
            )
            note.attachment_records.add(
                Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
            )
        notes = list(
            Note.objects.filter(owner=self.identity).prefetch_related(
                "attachment_records"
            )
        )
        assert len(notes) == 3
        with CaptureQueriesContext(connection) as ctx:
            for n in notes:
                assert len(n.attachment_list) == 1
        assert len(ctx.captured_queries) == 0, ctx.captured_queries


@pytest.mark.django_db(databases="__all__")
class TestCollectionApiLinking:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="colapi@test.com", username="colapi_user")
        self.identity = self.user.identity

    def test_create_and_update_link_brief_uploads(self):
        token = _api_token(self.user)
        bearer = f"Bearer {token}"
        client = Client()
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")

        created = client.post(
            "/api/me/collection/",
            data={"title": "C", "brief": f"![]({a.url})", "visibility": 0},
            content_type="application/json",
            HTTP_AUTHORIZATION=bearer,
        )
        assert created.status_code == 200, created.content
        uuid_ = created.json()["uuid"]
        collection = Collection.get_by_url(uuid_)
        assert collection is not None
        assert list(collection.attachment_records.all()) == [a]

        # and an edit that drops the image unlinks it
        updated = client.put(
            f"/api/me/collection/{uuid_}",
            data={"title": "C", "brief": "no image", "visibility": 0},
            content_type="application/json",
            HTTP_AUTHORIZATION=bearer,
        )
        assert updated.status_code == 200, updated.content
        assert collection.attachment_records.count() == 0


@pytest.mark.django_db(databases="__all__")
class TestExporterUsesRegistry:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="exp@test.com", username="exp_user")
        self.identity = self.user.identity
        self.item = Edition.objects.create(title="E Book")

    def test_pruned_post_note_exports_the_registered_copy(self, tmp_path):
        """With the post pruned, the legacy JSON points at dead takahe media
        while the registry holds our own live copy. The export must bundle
        the copy, not the dead URL."""
        from journal.exporters.ndjson import NdjsonExporter

        note = _save_quiet(
            Note(
                owner=self.identity,
                item=self.item,
                title="n",
                content="c",
                attachments=[
                    {
                        "type": "image",
                        "mimetype": "image/png",
                        "url": settings.TAKAHE_MEDIA_URL
                        + "attachments/2026/9/9/pruned.png",
                    }
                ],
            )
        )
        a = Attachment.register(
            self.identity, ContentFile(_png_bytes()), "png", mimetype="image/png"
        )
        note.attachment_records.add(a)
        assert note.latest_post is None  # nothing posted: stands in for a prune

        exporter = NdjsonExporter()
        exporter.user = self.user
        exporter.attachment_path = str(tmp_path)
        exporter.bundled_images = {}

        bundled = exporter._bundle_note_attachments(note)
        assert len(bundled) == 1
        # bundled by file, from the registry copy -- not a bare dead URL
        assert bundled[0].get("file")
        assert bundled[0]["mimetype"] == "image/png"


@pytest.mark.django_db(databases="__all__")
class TestDoubanImportLinking:
    """The Douban importer is the highest-volume upload path and the only
    Review write that bypasses update_item_review, so it registers its own
    images. Only the network boundary is mocked -- the fetch, the storage
    write and the linking all really run."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="db@test.com", username="db_user")
        self.identity = self.user.identity
        self.item = Edition.objects.create(title="Douban Book")

    def test_imported_review_images_are_registered_and_linked(self):
        from journal.importers.douban import DoubanImporter

        importer = DoubanImporter.create(
            user=self.user, visibility=0, file="unused.xlsx", mode=0
        )
        content = '<img src="https://img.douban.example/large/pic.jpg">'

        class _StubDownload:
            content = _png_bytes()

        class _StubDownloader:
            extention = "png"

            def __init__(self, url):
                pass

            def download(self):
                return _StubDownload()

        with (
            mock.patch.object(
                DoubanImporter, "guess_entity_url", return_value="https://d.example/1"
            ),
            mock.patch.object(
                DoubanImporter, "get_item_by_url", return_value=self.item
            ),
            mock.patch(
                "journal.importers.douban.ProxiedImageDownloader", _StubDownloader
            ),
            # the SSRF gate resolves DNS, which a .example host fails
            mock.patch("journal.importers.douban.is_valid_url", return_value=True),
        ):
            result = importer.import_review(
                "Douban Book",
                5,
                "A title",
                "https://d.example/review/1",
                content,
                timezone.now(),
            )

        assert result == 1
        review = Review.objects.get(owner=self.identity, item=self.item)
        rows = list(review.attachment_records.all())
        assert len(rows) == 1
        # fetched into our own storage under the importing user's prefix,
        # and linked to the review that embeds it
        assert _name(rows[0].file).startswith(f"upload/{self.identity.pk}/")
        assert default_storage.exists(_name(rows[0].file))
        assert _name(rows[0].file) in review.body


@pytest.mark.django_db(databases="__all__")
class TestAccountDeletion:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="del@test.com", username="del_user")
        self.identity = self.user.identity

    def test_removing_identity_data_deletes_uploads_and_covers(self):
        a = Attachment.register(self.identity, ContentFile(_png_bytes()), "png")
        upload_name = _name(a.file)
        article = Article.update_local_article(
            owner=self.identity,
            title="With cover",
            body="body",
            cover=SimpleUploadedFile("c.png", _png_bytes(), "image/png"),
        )
        cover_name = _name(article.cover)
        assert default_storage.exists(upload_name)
        assert default_storage.exists(cover_name)

        remove_data_by_identity(self.identity)

        assert not Attachment.objects.filter(owner=self.identity).exists()
        assert not default_storage.exists(upload_name)
        assert not default_storage.exists(cover_name)
