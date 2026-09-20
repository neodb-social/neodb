"""Storage for the files a Task takes in or produces.

An import is uploaded by the web process and read by a worker; an export is
written by a worker and downloaded from the web process. Neither is media, but
both have to be reachable from whichever process handles the next step, and a
deployment may run those on separate hosts with no shared volume. So they live
in the media storage rather than on local disk.

Paths recorded in ``Task.metadata`` before this moved were absolute paths under
MEDIA_ROOT. They are still read, served and deleted from disk, so a task that
was already queued when the new code shipped still completes.
"""

import contextlib
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import IO, Any

from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.files.storage import default_storage

from common.utils import GenerateDateUUIDMediaFilePath, S3Storage

logger = logging.getLogger(__name__)

#: How long a download link stays valid. Long enough to start a large
#: download, short enough that a link copied out of a browser's history is
#: useless: an export archive carries the owner's actor private key.
DOWNLOAD_URL_EXPIRY = 300


def is_stored(path: str) -> bool:
    """True when ``path`` is a storage key rather than a local file.

    Keys are relative, and the absolute paths recorded before the files moved
    into storage are not. A relative path that resolves to a real file is
    local too: a management command or a test may pass one, and reading it
    from disk is what the caller meant. A key never collides with that,
    because ``sync/`` and ``export/`` do not exist below the working
    directory.
    """
    if not path or os.path.isabs(path):
        return False
    return not os.path.isfile(path)


def save_task_file(content: File, filename: str, path_root: str) -> str:
    """Store ``content`` under a dated random key, returning that key."""
    rel_path = GenerateDateUUIDMediaFilePath(filename, path_root)
    return default_storage.save(rel_path, content)


def save_local_file(local_path: str, filename: str, path_root: str) -> str:
    """Store a file already written to local disk, returning its storage key."""
    with open(local_path, "rb") as f:
        return save_task_file(File(f), filename, path_root)


def open_task_file(path: str, mode: str = "rb") -> IO[Any]:
    if is_stored(path):
        return default_storage.open(path, mode)
    return open(path, mode)


def read_task_file(path: str) -> bytes:
    """The whole file, for callers that want bytes rather than a handle."""
    with open_task_file(path) as f:
        return f.read()


def exists(path: str) -> bool:
    if not path:
        return False
    if is_stored(path):
        return default_storage.exists(path)
    return os.path.isfile(path)


def overwrite_task_file(path: str, content: bytes) -> None:
    """Replace the bytes at ``path``, keeping the same key.

    ``Storage.save`` never overwrites -- it would pick a suffixed name and
    leave every recorded reference pointing at the stale object -- so the old
    one goes first.
    """
    if not is_stored(path):
        with open(path, "wb") as f:
            f.write(content)
        return
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(content))


def copy_task_file(src: str, dst: str) -> str:
    """Copy within whichever backend holds ``src``, returning the real name.

    ``dst`` is derived from a UUID key so it will not collide, but ``save``
    picks its own name if it ever does, and that name is what the caller has
    to record.
    """
    if not is_stored(src):
        shutil.copyfile(src, dst)
        return dst
    with default_storage.open(src, "rb") as f:
        return default_storage.save(dst, File(f))


def stage_locally(path: str) -> str:
    """Copy a stored object to a temp file and return its path.

    The caller owns the file; ``Task.local_file`` ties it to the task's
    lifetime. Use ``local_copy`` instead wherever the scope is a block.
    """
    suffix = os.path.splitext(path)[1]
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        with default_storage.open(path, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    return tmp


def discard(path: str) -> None:
    """Remove a staged temp file, ignoring a file that is already gone."""
    with contextlib.suppress(OSError):
        os.remove(path)


@contextlib.contextmanager
def local_copy(path: str) -> Iterator[str]:
    """Yield a real filesystem path for ``path``.

    ``zipfile``, ``lxml.etree.parse`` and ``csv`` all want a path or a seekable
    file, and an importer may walk the archive repeatedly, so a remote object
    is staged to a temp file for the duration rather than streamed.
    """
    if not is_stored(path):
        yield path
        return
    tmp = stage_locally(path)
    try:
        yield tmp
    finally:
        discard(tmp)


def delete_task_file(path: str) -> bool:
    """Delete a task's file wherever it lives. Used by every cleanup path."""
    if not path:
        return False
    if is_stored(path):
        try:
            if default_storage.exists(path):
                default_storage.delete(path)
                logger.debug(f"Deleted stored file {path}")
                return True
        except Exception as e:
            logger.warning(f"Failed to delete stored file {path}: {e}")
        return False
    return _delete_local_path(path)


def _delete_local_path(file_path: str) -> bool:
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
            logger.debug(f"Deleted file {file_path}")
            # Remove parent directories if empty (date-based dirs like 2024/01/15/)
            parent = os.path.dirname(file_path)
            for _ in range(3):  # up to 3 levels (day/month/year)
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


def download_url(path: str) -> str | None:
    """A URL the browser can fetch the file from directly, if there is one.

    None on a local backend, where the caller streams the file instead.

    On S3 this is deliberately not ``default_storage.url()``: NeoDB always sets
    AWS_S3_CUSTOM_DOMAIN, and django-storages returns an unsigned public URL
    whenever a custom domain is set. An export carries the owner's actor
    private key, so it gets a signed URL against the bucket endpoint instead,
    and the object never has to be publicly readable.
    """
    if not is_stored(path):
        return None
    if not settings.MEDIA_BACKEND.startswith("s3"):
        return None
    try:
        signer = S3Storage(custom_domain=None, querystring_auth=True)
        return signer.url(path, expire=DOWNLOAD_URL_EXPIRY)
    except Exception as e:
        logger.warning(f"Failed to sign a download URL for {path}: {e}")
        return None
