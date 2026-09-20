"""Helpers for the media files that web and worker processes exchange.

Import uploads (``sync/``) and generated exports (``export/``) go through
``default_storage``, which is the local filesystem or S3 depending on
``MEDIA_BACKEND``. Web and worker may run on different hosts, so what a task
carries in its metadata is a storage key, never a filesystem path.

Paths written before s3 support are absolute and under ``MEDIA_ROOT``, and
keep working: every reader here maps such a path back to its key, and an
absolute path outside ``MEDIA_ROOT`` is passed through to the filesystem
as-is.
"""

import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.utils import timezone

from common.utils import GenerateDateUUIDMediaFilePath

logger = logging.getLogger(__name__)


def media_key(path: str) -> str:
    """The storage key for a file path kept in task metadata."""
    root = settings.MEDIA_ROOT.rstrip("/") + "/"
    return path[len(root) :] if path.startswith(root) else path


def generate_media_key(path_root: str, filename: str) -> str:
    """A hard to guess key under ``path_root`` that keeps ``filename``.

    The uuid is a directory of its own, so the key still ends with a name
    worth showing: a remote backend serves a download by redirecting to the
    object, and the key is all the browser has to name the file by.
    """
    root = path_root if path_root.endswith("/") else path_root + "/"
    return root + timezone.now().strftime("%Y/%m/%d") + f"{uuid.uuid4()}/{filename}"


def media_exists(path: str) -> bool:
    key = media_key(path)
    if os.path.isabs(key):
        return os.path.exists(key)
    return default_storage.exists(key)


def media_url(path: str) -> str:
    return default_storage.url(media_key(path))


def local_media_path(path: str) -> str | None:
    """The filesystem path of a stored file, or None on a remote backend."""
    key = media_key(path)
    if os.path.isabs(key):
        return key
    try:
        return default_storage.path(key)
    except NotImplementedError:
        return None


def save_upload(upload: File, path_root: str, filename: str) -> str:
    """Store an uploaded file under ``path_root``; returns its storage key."""
    return default_storage.save(
        GenerateDateUUIDMediaFilePath(filename, path_root), upload
    )


def save_media_file(local_path: str, key: str) -> str:
    """Copy a local file into storage at exactly ``key``, replacing any object.

    ``Storage.save()`` renames around a collision, so an object already there
    is removed first: the key is the one the caller has recorded.
    """
    if default_storage.exists(key):
        default_storage.delete(key)
    with open(local_path, "rb") as f:
        saved = default_storage.save(key, File(f))
    if saved != key:
        logger.warning(f"stored {local_path} as {saved}, not {key}")
    return saved


def open_media(path: str, mode: str = "rb") -> File:
    """Open a stored file for reading."""
    local = local_media_path(path)
    if local is not None:
        return File(open(local, mode))
    return default_storage.open(media_key(path), mode)


def delete_media(path: str) -> bool:
    """Delete a stored file. Returns True if something was deleted."""
    key = media_key(path)
    local = local_media_path(path)
    if local is not None:
        # prune only the directories the key itself introduced, never one
        # above them: a path from outside the media root introduced none
        levels = 0 if os.path.isabs(key) else key.count("/")
        return _delete_local_path(local, levels)
    if not default_storage.exists(key):
        return False
    default_storage.delete(key)
    logger.debug(f"Deleted {key}")
    return True


def _delete_local_path(file_path: str, prune_levels: int = 0) -> bool:
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
            logger.debug(f"Deleted file {file_path}")
            # Remove parent directories if empty (uuid and date dirs)
            parent = os.path.dirname(file_path)
            for _ in range(prune_levels):
                if parent and os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
                    logger.debug(f"Removed empty directory {parent}")
                    parent = os.path.dirname(parent)
                else:
                    break
            return True
        elif os.path.isdir(file_path):
            shutil.rmtree(file_path)
            logger.debug(f"Deleted directory {file_path}")
            return True
    except OSError as e:
        logger.warning(f"Failed to delete {file_path}: {e}")
    return False


def download_media_file(path: str, dest_dir: str) -> str:
    """Copy a stored file into ``dest_dir`` and return the local path."""
    key = media_key(path)
    local = os.path.join(dest_dir, os.path.basename(key) or "file")
    with default_storage.open(key, "rb") as src, open(local, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return local


@contextmanager
def local_media_file(path: str, writable: bool = False) -> Iterator[str]:
    """Yield a filesystem path for a stored file.

    On a local backend that is the file itself. On a remote one it is a
    temporary copy, stored back under the same key on a clean exit when
    ``writable``.
    """
    local = local_media_path(path)
    if local is not None:
        yield local
        return
    temp_dir = tempfile.mkdtemp(prefix="neodb-media-")
    try:
        local = download_media_file(path, temp_dir)
        yield local
        if writable:
            save_media_file(local, media_key(path))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@contextmanager
def media_file_writer(key: str) -> Iterator[str]:
    """Yield a filesystem path to write; its content ends up stored at ``key``.

    On a local backend the file is written in place, so a large export is not
    copied a second time.
    """
    local = local_media_path(key)
    if local is not None:
        os.makedirs(os.path.dirname(local), exist_ok=True)
        yield local
        return
    temp_dir = tempfile.mkdtemp(prefix="neodb-media-")
    try:
        local = os.path.join(temp_dir, os.path.basename(key) or "file")
        yield local
        save_media_file(local, key)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
