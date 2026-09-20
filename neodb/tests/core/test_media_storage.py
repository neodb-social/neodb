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
from django.test import Client
from django.urls import reverse

from common.storage import media_exists, media_key, media_url, save_upload
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

    def test_path_outside_media_root_is_left_alone(self, tmp_path):
        outside = tmp_path.parent / "elsewhere.csv"
        outside.write_text("Title,Author\n")
        task = GoodreadsImporter.create(self.user, visibility=0, file=str(outside))
        assert task.local_path() == str(outside)
