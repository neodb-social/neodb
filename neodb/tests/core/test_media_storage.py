"""The sync/ and export/ trees on a media backend with no local path.

S3 is such a backend: ``Storage.path()`` raises, so a worker cannot open an
upload the web process wrote, and the web process cannot open an export a
worker wrote. Both go through ``default_storage`` instead, and what a task
carries is a key. ``InMemoryStorage`` is no stand-in here, because it answers
``path()`` with a filesystem path it never writes to.
"""

import os
import zipfile

import pytest
from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import Storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import Client, override_settings
from django.urls import reverse

from common.apps import media_url_errors
from common.media_url import media_url_at_site_root, s3_custom_domain
from common.storage import (
    media_exists,
    media_key,
    media_url,
    open_media,
    save_media_file,
    save_upload,
)
from common.utils import S3Storage
from journal.exporters import NdjsonExporter
from journal.importers import GoodreadsImporter
from users.models import Task, User


class RemoteStorage(Storage):
    """A backend that keeps files, but refuses to name a local path.

    Shaped like ``S3Storage``: it stores and serves, and ``path()`` raises.
    Files go to a directory, because a test has to read them back.
    """

    def __init__(self, location: str, base_url: str) -> None:
        self._location = location
        self._base_url = base_url

    def _full(self, name: str) -> str:
        return os.path.join(self._location, name)

    def path(self, name):
        raise NotImplementedError("This backend doesn't support absolute paths.")

    def _open(self, name, mode="rb"):
        return File(open(self._full(name), mode))

    def get_available_name(self, name, max_length=None):
        # S3 overwrites a key rather than renaming around it
        return name

    def _save(self, name, content):
        full = self._full(name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            for chunk in content.chunks():
                f.write(chunk)
        return name

    def exists(self, name):
        return os.path.exists(self._full(name))

    def delete(self, name):
        os.remove(self._full(name))

    def size(self, name):
        return os.path.getsize(self._full(name))

    def url(self, name):
        return self._base_url + name


@pytest.fixture
def remote_storage(settings, tmp_path):
    storages = dict(settings.STORAGES)
    storages["default"] = {
        "BACKEND": "tests.core.test_media_storage.RemoteStorage",
        "OPTIONS": {
            "location": str(tmp_path),
            "base_url": "https://media.example.org/",
        },
    }
    settings.STORAGES = storages
    return tmp_path


@pytest.mark.django_db(databases="__all__")
class TestRemoteMediaBackend:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="s3@test.com", username="s3user")

    def test_upload_is_stored_under_a_key(self, remote_storage):
        key = save_upload(
            SimpleUploadedFile("goodreads.csv", b"Title,Author\nDune,Herbert\n"),
            settings.SYNC_FILE_PATH_ROOT,
            "x.csv",
        )
        assert key.startswith(settings.SYNC_FILE_PATH_ROOT)
        assert not os.path.isabs(key)
        assert media_exists(key)

    def test_task_reads_an_upload_it_cannot_open(self, remote_storage):
        key = save_upload(
            SimpleUploadedFile("goodreads.csv", b"Title,Author\nDune,Herbert\n"),
            settings.SYNC_FILE_PATH_ROOT,
            "x.csv",
        )
        task = GoodreadsImporter.create(self.user, visibility=0, file=key)
        local = task.local_path()
        assert os.path.isabs(local)
        with open(local, "rb") as f:
            assert f.read() == b"Title,Author\nDune,Herbert\n"
        # the same copy is reused, and dropped when the run ends
        assert task.local_path() == local
        task.drop_local_copies()
        assert not os.path.exists(local)

    def test_task_deletes_a_stored_file(self, remote_storage):
        key = save_upload(
            SimpleUploadedFile("goodreads.csv", b"Title,Author\n"),
            settings.SYNC_FILE_PATH_ROOT,
            "x.csv",
        )
        task = GoodreadsImporter.create(self.user, visibility=0, file=key)
        assert task.delete_files() is True
        assert not media_exists(key)

    def test_a_file_written_before_s3_is_read_from_disk(
        self, remote_storage, settings, tmp_path
    ):
        """An instance that already ran on s3 wrote its uploads locally."""
        settings.MEDIA_ROOT = str(tmp_path / "media")
        old = tmp_path / "media" / "sync" / "2024" / "01" / "01x.csv"
        old.parent.mkdir(parents=True)
        old.write_text("Title,Author\n")
        task = GoodreadsImporter.create(self.user, visibility=0, file=str(old))
        # nothing of it is in the bucket, and it still has to import
        assert not media_exists(str(tmp_path / "media" / "sync" / "gone.csv"))
        assert media_exists(str(old))
        assert task.local_path() == str(old)
        assert task.delete_files() is True

    def test_a_replacement_lands_on_the_same_key(self, remote_storage, tmp_path):
        key = save_upload(
            SimpleUploadedFile("x.csv", b"one\n"), settings.SYNC_FILE_PATH_ROOT, "x.csv"
        )
        edited = tmp_path / "edited.csv"
        edited.write_text("two\n")
        assert save_media_file(str(edited), key) == key
        with open_media(key) as f:
            assert f.read() == b"two\n"

    def test_a_failed_replacement_keeps_the_old_object(
        self, remote_storage, tmp_path, monkeypatch
    ):
        """A matched file holds every row the user has corrected so far."""
        key = save_upload(
            SimpleUploadedFile("x.csv", b"one\n"), settings.SYNC_FILE_PATH_ROOT, "x.csv"
        )
        edited = tmp_path / "edited.csv"
        edited.write_text("two\n")

        def _fail(self, name, content):
            raise OSError("upload failed")

        monkeypatch.setattr(RemoteStorage, "_save", _fail)
        with pytest.raises(OSError):
            save_media_file(str(edited), key)
        monkeypatch.undo()
        with open_media(key) as f:
            assert f.read() == b"one\n"

    def test_export_is_stored_and_served_by_redirect(self, remote_storage):
        exporter = NdjsonExporter.create(self.user)
        exporter.run()
        key = exporter.metadata["file"]
        assert key.startswith(settings.EXPORT_FILE_PATH_ROOT)
        # the key ends with the download's name: a redirect carries no
        # Content-Disposition of its own
        assert key.endswith(f"/{exporter.filename}.zip")
        assert media_exists(key)
        with zipfile.ZipFile(exporter.local_path()) as zf:
            assert "journal.ndjson" in zf.namelist()

        exporter.state = Task.States.complete
        exporter.save(update_fields=["state"])
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(
            reverse("users:user_task_download", args=("journal.ndjsonexporter",))
        )
        assert response.status_code == 302
        assert response["Location"] == media_url(key)


@pytest.mark.django_db(databases="__all__")
class TestLocalMediaBackend:
    """The local backend keeps serving downloads through nginx."""

    @pytest.fixture(autouse=True)
    def setup_data(self, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        self.user = User.register(email="local@test.com", username="localuser")

    def test_export_download_is_accelerated(self):
        exporter = NdjsonExporter.create(self.user)
        exporter.run()
        key = exporter.metadata["file"]
        exporter.state = Task.States.complete
        exporter.save(update_fields=["state"])
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(
            reverse("users:user_task_download", args=("journal.ndjsonexporter",))
        )
        assert response.status_code == 200
        assert response["X-Accel-Redirect"] == settings.MEDIA_URL + key
        assert f"{exporter.filename}.zip" in response["Content-Disposition"]

    def test_path_stored_before_s3_support_still_resolves(self, tmp_path):
        old = tmp_path / "sync" / "2024" / "01" / "01x.csv"
        old.parent.mkdir(parents=True)
        old.write_text("Title,Author\n")
        assert media_key(str(old)) == "sync/2024/01/01x.csv"
        task = GoodreadsImporter.create(self.user, visibility=0, file=str(old))
        assert task.local_path() == str(old)
        assert task.delete_files() is True
        # the emptied date directories go too, the media root never does
        assert not (tmp_path / "sync").exists()
        assert tmp_path.exists()

    def test_deleting_a_file_from_elsewhere_keeps_its_directory(self, tmp_path):
        outside = tmp_path.parent / "elsewhere" / "export.csv"
        outside.parent.mkdir(exist_ok=True)
        outside.write_text("Title,Author\n")
        task = GoodreadsImporter.create(self.user, visibility=0, file=str(outside))
        assert task.delete_files() is True
        assert outside.parent.exists()

    def test_a_replacement_keeps_the_key_and_the_old_content_until_it_lands(
        self, tmp_path
    ):
        key = "export/sitemap.txt"
        first = tmp_path / "first.txt"
        first.write_text("one\n")
        assert save_media_file(str(first), key) == key
        second = tmp_path / "second.txt"
        second.write_text("two\n")
        assert save_media_file(str(second), key) == key
        # the local backend renames around a collision, so a stray copy of
        # either write beside the key would mean the swap did not happen
        assert sorted(p.name for p in (tmp_path / "export").iterdir()) == [
            "sitemap.txt"
        ]
        with open_media(key) as f:
            assert f.read() == b"two\n"

    def test_path_outside_media_root_is_left_alone(self, tmp_path):
        outside = tmp_path.parent / "elsewhere.csv"
        outside.write_text("Title,Author\n")
        task = GoodreadsImporter.create(self.user, visibility=0, file=str(outside))
        assert task.local_path() == str(outside)


class TestS3CustomDomain:
    """Which host, if any, django-storages builds a media url from.

    Media on one of our own domains is addressed by path, so a page served on
    an alias domain loads media from that same domain. A media host of its own
    keeps the absolute url it always had.
    """

    def test_the_site_domain_yields_a_path(self):
        assert s3_custom_domain("https://example.com/m/", ["example.com"]) == "/m"

    def test_the_hostname_comparison_ignores_case(self):
        domains = ["example.com", "alias.example.org"]
        assert s3_custom_domain("https://Example.COM/m/", domains) == "/m"

    def test_an_alternative_domain_yields_a_path(self):
        domains = ["example.com", "alias.example.org"]
        assert s3_custom_domain("https://alias.example.org/m/", domains) == "/m"

    def test_a_media_host_of_its_own_is_kept(self):
        assert (
            s3_custom_domain("https://media.example.net/media/", ["example.com"])
            == "media.example.net/media"
        )

    def test_a_port_is_not_part_of_the_comparison(self):
        assert s3_custom_domain("https://example.com:8443/m/", ["example.com"]) == "/m"

    def test_a_host_less_media_url_yields_the_same_path(self):
        # characterisation: "/m/" is the MEDIA_URL default, and the storage
        # has to make something of it rather than build "https:///m/..."
        assert s3_custom_domain("/m/", ["example.com"]) == "/m"

    def test_the_site_root_yields_the_root_path(self):
        # a misconfiguration, reported by neodb.E005; the value still has to
        # be a path so the storage does not fall back to an endpoint url
        assert s3_custom_domain("https://example.com/", ["example.com"]) == "/"
        assert s3_custom_domain("https://example.com", ["example.com"]) == "/"


class TestS3StorageUrl:
    """``S3Storage.url`` with a path-only custom domain.

    Constructed directly rather than through ``override_settings``, because
    ``AWS_S3_CUSTOM_DOMAIN`` is computed once, when settings are imported.
    """

    def test_a_path_custom_domain_stays_a_path(self):
        assert S3Storage(custom_domain="/m").url("item/x.jpg") == "/m/item/x.jpg"

    def test_a_name_is_percent_encoded(self):
        storage = S3Storage(custom_domain="/m")
        assert storage.url("item/a b.jpg") == "/m/item/a%20b.jpg"
        assert storage.url("item/封面.jpg") == ("/m/item/%E5%B0%81%E9%9D%A2.jpg")

    def test_the_site_root_yields_a_single_slash(self):
        # "//item/x.jpg" would be protocol relative, and resolve against
        # whatever host the page came from with the wrong meaning
        assert S3Storage(custom_domain="/").url("item/x.jpg") == "/item/x.jpg"

    def test_parameters_are_kept(self):
        storage = S3Storage(custom_domain="/m")
        assert storage.url("item/x.jpg", parameters={"v": "2"}) == "/m/item/x.jpg?v=2"

    def test_a_media_host_keeps_the_parent_behaviour(self):
        storage = S3Storage(custom_domain="media.example.net/media")
        assert storage.url("item/x.jpg") == "https://media.example.net/media/item/x.jpg"


class TestMediaUrlAtSiteRoot:
    def test_a_path_is_not_the_root(self):
        assert (
            media_url_at_site_root("https://example.com/m/", ["example.com"]) is False
        )

    def test_a_media_host_at_its_own_root_is_allowed(self):
        assert (
            media_url_at_site_root("https://media.example.net/", ["example.com"])
            is False
        )

    def test_the_site_root_is_reported(self):
        assert media_url_at_site_root("https://example.com/", ["example.com"]) is True

    def test_a_host_less_root_is_reported(self):
        assert media_url_at_site_root("/", ["example.com"]) is True


class TestMediaUrlCheck:
    """``neodb.E005``: a remote backend served from the root of our own site.

    Every media key would sit in the url space of the site itself. Settings do
    not raise on it, so that ``neodb-manage check`` runs far enough to say so.
    """

    @override_settings(
        MEDIA_BACKEND="s3://key:secret@s3.example:9000/bucket",
        MEDIA_URL="https://example.org/",
        SITE_DOMAINS=["example.org"],
    )
    def test_the_site_root_is_an_error(self):
        assert [m.id for m in media_url_errors()] == ["neodb.E005"]

    @override_settings(
        MEDIA_BACKEND="s3://key:secret@s3.example:9000/bucket",
        MEDIA_URL="https://alias.example.org/",
        SITE_DOMAINS=["example.org", "alias.example.org"],
    )
    def test_an_alias_root_is_an_error(self):
        assert [m.id for m in media_url_errors()] == ["neodb.E005"]

    @override_settings(
        MEDIA_BACKEND="s3://key:secret@s3.example:9000/bucket",
        MEDIA_URL="https://example.org/m/",
        SITE_DOMAINS=["example.org"],
    )
    def test_a_path_on_the_site_domain_passes(self):
        assert media_url_errors() == []

    @override_settings(
        MEDIA_BACKEND="s3://key:secret@s3.example:9000/bucket",
        MEDIA_URL="https://media.example.net/",
        SITE_DOMAINS=["example.org"],
    )
    def test_a_media_host_at_its_own_root_passes(self):
        assert media_url_errors() == []

    @override_settings(
        MEDIA_BACKEND="local://",
        MEDIA_URL="/",
        SITE_DOMAINS=["example.org"],
    )
    def test_the_local_backend_is_not_checked(self):
        # the local backend serves MEDIA_ROOT at MEDIA_URL, and that overlap
        # is the web server's configuration rather than ours
        assert media_url_errors() == []

    @override_settings(
        MEDIA_BACKEND="s3://key:secret@s3.example:9000/bucket",
        MEDIA_URL="https://example.org/",
        SITE_DOMAINS=["example.org"],
    )
    def test_manage_check_reports_it(self):
        # the registration itself, so the error reaches neodb-manage check
        with pytest.raises(SystemCheckError, match="neodb.E005"):
            call_command("check")
