"""The storage the import/export files live in, and the cleanup that wipes them.

These files are not media: an import is uploaded by the web process and read by
a worker, an export is written by a worker and downloaded by the web process.
A deployment may run those on different hosts, so they go to the media storage
rather than a local volume -- and the cleanup has to follow them there.
"""

import os
from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from django.utils import timezone

from journal.exporters import NdjsonExporter
from users.jobs.cleanup import prune_tasks
from users.models import Task, User
from users.models.task_files import (
    copy_task_file,
    delete_task_file,
    discard,
    download_url,
    exists,
    is_stored,
    local_copy,
    overwrite_task_file,
    save_task_file,
    stage_locally,
)

pytestmark = pytest.mark.django_db(databases="__all__")


def _user(username: str) -> User:
    return User.register(email=f"{username}@example.com", username=username)


class TestPathKinds:
    def test_a_storage_key_is_relative_and_a_legacy_path_absolute(self):
        assert is_stored("sync/2026/09/19/abc.csv")
        assert not is_stored("/www/m/sync/2026/09/19/abc.csv")
        assert not is_stored("")

    def test_a_relative_path_to_a_real_file_is_local(self, tmp_path, monkeypatch):
        """A management command or a test may pass one, and it is not a key."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "test_data").mkdir()
        (tmp_path / "test_data" / "export.csv").write_text("a,b\n")

        assert not is_stored("test_data/export.csv")
        with local_copy("test_data/export.csv") as local:
            assert local == "test_data/export.csv"


class TestRoundTrip:
    def test_saved_file_is_readable_and_deletable(self, tmp_path):
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"payload"), "x.csv", "sync/")

            assert is_stored(key)
            assert key.startswith("sync/")
            assert exists(key)
            with default_storage.open(key, "rb") as f:
                assert f.read() == b"payload"

            assert delete_task_file(key)
            assert not exists(key)

    def test_staging_copies_out_and_keeps_the_extension(self, tmp_path):
        """What a remote backend goes through, where there is no path to read.
        zipfile and openpyxl want a seekable file, and an importer walks the
        archive more than once."""
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"zipbytes"), "x.zip", "sync/")

            staged = stage_locally(key)
            try:
                assert os.path.isabs(staged)
                assert staged.endswith(".zip")
                assert staged != str(tmp_path / key)
                with open(staged, "rb") as f:
                    assert f.read() == b"zipbytes"
            finally:
                discard(staged)

            assert not os.path.exists(staged)

    def test_local_backend_is_read_in_place_without_a_copy(self, tmp_path):
        """local:// is the default and must stay cheap: the file is already on
        this disk, and staging it would put a second copy of a whole import
        archive beside it."""
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"archive"), "x.zip", "sync/")

            with local_copy(key) as local:
                assert local == str(tmp_path / key)
                assert os.path.isfile(local)

            # still there: it is the stored file, not a staged copy
            assert os.path.isfile(str(tmp_path / key))
            assert exists(key)

    def test_task_local_file_points_at_the_stored_file_on_local_backend(self, tmp_path):
        user = _user("localreader")
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"archive"), "x.zip", "sync/")
            task = NdjsonExporter.create(user=user, file=key)

            assert task.local_file == str(tmp_path / key)
            with open(task.local_file, "rb") as f:
                assert f.read() == b"archive"

    def test_local_copy_passes_a_legacy_path_straight_through(self, tmp_path):
        legacy = tmp_path / "old.csv"
        legacy.write_bytes(b"legacy")

        with local_copy(str(legacy)) as local:
            assert local == str(legacy)

        # never deleted: it is the real file, not a staged copy
        assert legacy.exists()

    def test_overwrite_keeps_the_key(self, tmp_path):
        """A suffixed name would leave every recorded reference on the old bytes."""
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"first"), "x.csv", "sync/")

            overwrite_task_file(key, b"second")

            with default_storage.open(key, "rb") as f:
                assert f.read() == b"second"
            assert sorted(p.name for p in (tmp_path / "sync").rglob("*.csv")) == [
                os.path.basename(key)
            ]

    def test_a_failed_overwrite_leaves_the_old_bytes(self, tmp_path, monkeypatch):
        """It rewrites a matched CSV one edited row at a time. Deleting first
        and then failing to upload would lose every match made so far."""
        legacy = tmp_path / "matched.csv"
        legacy.write_bytes(b"title,link\nDune,x\n")

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("users.models.task_files.os.replace", boom)
        with pytest.raises(OSError):
            overwrite_task_file(str(legacy), b"clobbered")

        assert legacy.read_bytes() == b"title,link\nDune,x\n"
        # the staging file does not pile up beside it either
        assert [p.name for p in tmp_path.iterdir()] == ["matched.csv"]

    def test_copy_duplicates_within_storage(self, tmp_path):
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            src = save_task_file(ContentFile(b"rows"), "x.csv", "sync/")
            dst = os.path.join(os.path.dirname(src), "copy-matched.csv")

            saved = copy_task_file(src, dst)

            assert saved == dst
            assert exists(src) and exists(dst)


class TestDownloadUrl:
    """A signed link is only handed out when the browser can actually use it."""

    def test_no_url_on_a_local_backend(self, tmp_path):
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"x"), "f.zip", "export/")
            assert download_url(key, "export.zip", "application/zip") is None

    def test_no_url_for_a_legacy_local_path(self, tmp_path):
        legacy = tmp_path / "old.zip"
        legacy.write_bytes(b"x")
        assert download_url(str(legacy), "export.zip", "application/zip") is None

    def test_an_internal_endpoint_alone_yields_no_url(self):
        """Every self-hosted setup in the docs points MEDIA_BACKEND at an
        internal host and serves media from a different one. Signing against
        the internal host would hand the browser an unreachable link, and
        because signing succeeds the streaming fallback would never run."""
        with override_settings(
            MEDIA_BACKEND="s3-insecure://k:s@minio:9000/media",
            AWS_S3_ENDPOINT_URL="http://minio:9000",
            MEDIA_BACKEND_S3_PUBLIC_ENDPOINT="",
        ):
            assert download_url("export/x.zip", "export.zip", "application/zip") is None


class TestCleanup:
    """What the user asked for: expired files actually leave the bucket."""

    def _stale_export(self, user: User, key: str) -> NdjsonExporter:
        task = NdjsonExporter.create(user=user)
        task.metadata["file"] = key
        task.state = Task.States.complete
        task.save()
        # created_time is auto_now_add, so age it by hand
        Task.objects.filter(pk=task.pk).update(
            created_time=timezone.now() - timedelta(days=60)
        )
        return task

    def test_pruning_deletes_the_stored_object_not_just_the_row(self, tmp_path):
        user = _user("pruned")
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"archive"), "f.zip", "export/")
            task = self._stale_export(user, key)

            tasks_deleted, files_deleted = prune_tasks(days=28)

            assert tasks_deleted >= 1
            assert files_deleted >= 1
            assert not exists(key)
            assert not Task.objects.filter(pk=task.pk).exists()

    def test_pruning_spares_a_task_inside_the_window(self, tmp_path):
        user = _user("recent")
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"archive"), "f.zip", "export/")
            task = NdjsonExporter.create(user=user)
            task.metadata["file"] = key
            task.state = Task.States.complete
            task.save()

            prune_tasks(days=28)

            assert exists(key)
            assert Task.objects.filter(pk=task.pk).exists()

    def test_deleting_a_user_wipes_their_stored_files(self, tmp_path):
        user = _user("goner")
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            key = save_task_file(ContentFile(b"archive"), "f.zip", "export/")
            task = NdjsonExporter.create(user=user)
            task.metadata["file"] = key
            task.save()

            task.delete_files()

            assert not exists(key)

    def test_cleanup_still_removes_a_legacy_local_file(self, tmp_path):
        user = _user("legacyprune")
        legacy = tmp_path / "export" / "old.zip"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"old archive")
        self._stale_export(user, str(legacy))

        _, files_deleted = prune_tasks(days=28)

        assert files_deleted >= 1
        assert not legacy.exists()
