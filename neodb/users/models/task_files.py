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
import functools
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import IO, Any
from urllib.parse import quote

from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage, default_storage

from common.utils import GenerateDateUUIDMediaFilePath, S3Storage

logger = logging.getLogger(__name__)

#: How long a download link stays valid. Long enough to start a large
#: download, short enough that a link copied out of a browser's history is
#: useless: an export archive carries the owner's actor private key.
DOWNLOAD_URL_EXPIRY = 300


def _on_s3() -> bool:
    return settings.MEDIA_BACKEND.startswith("s3")


@functools.cache
def _s3_task_storage() -> S3Storage:
    """Private S3 storage for task files.

    Media is uploaded public-read, which would make an export archive -- and
    the account private key inside it -- readable by anyone who guesses or is
    handed the key. Signing a URL cannot undo that, because dropping the query
    string from a public object still fetches it. So task files get their own
    storage that uploads them private, and the signed link is the only way in.

    An empty configured ACL means the bucket has ACLs disabled and is governed
    by a policy instead; sending "private" there is rejected, so the ACL is
    only tightened when one is in use at all.
    """
    acl = "private" if settings.MEDIA_BACKEND_S3_ACL else None
    return S3Storage(default_acl=acl, querystring_auth=True)


def _storage() -> Storage:
    """Where task files live.

    Locally this is the media storage, so MEDIA_ROOT overrides still apply.
    """
    return _s3_task_storage() if _on_s3() else default_storage


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
    return _storage().save(rel_path, content)


def save_local_file(local_path: str, filename: str, path_root: str) -> str:
    """Store a file already written to local disk, returning its storage key."""
    with open(local_path, "rb") as f:
        return save_task_file(File(f), filename, path_root)


def open_task_file(path: str, mode: str = "rb") -> IO[Any]:
    if is_stored(path):
        return _storage().open(path, mode)
    return open(path, mode)


def read_task_file(path: str) -> bytes:
    """The whole file, for callers that want bytes rather than a handle."""
    with open_task_file(path) as f:
        return f.read()


def exists(path: str) -> bool:
    if not path:
        return False
    if is_stored(path):
        return _storage().exists(path)
    return os.path.isfile(path)


def overwrite_task_file(path: str, content: bytes) -> None:
    """Replace the bytes at ``path``, keeping the same key.

    Never deletes first. This is how a single edited row is written back to a
    matched CSV, and a delete followed by a failed upload would lose every
    match and hand-edit made so far, with previews and downloads meanwhile
    seeing nothing there at all.

    Locally the replacement is atomic through ``os.replace``. On S3 it is a
    plain overwriting PUT: ``AWS_S3_FILE_OVERWRITE`` defaults to true, so
    ``save`` keeps the key instead of picking a suffixed name, and the object
    only changes once the upload succeeds.
    """
    if not is_stored(path):
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception:
            discard(tmp)
            raise
        return
    _storage().save(path, ContentFile(content))


def copy_task_file(src: str, dst: str) -> str:
    """Copy within whichever backend holds ``src``, returning the real name.

    ``dst`` is derived from a UUID key so it will not collide, but ``save``
    picks its own name if it ever does, and that name is what the caller has
    to record.
    """
    if not is_stored(src):
        shutil.copyfile(src, dst)
        return dst
    with _storage().open(src, "rb") as f:
        return _storage().save(dst, File(f))


def stage_locally(path: str) -> str:
    """Copy a stored object to a temp file and return its path.

    The caller owns the file; ``Task.local_file`` ties it to the task's
    lifetime. Use ``local_copy`` instead wherever the scope is a block.
    """
    suffix = os.path.splitext(path)[1]
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        with _storage().open(path, "rb") as src, open(tmp, "wb") as dst:
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
            if _storage().exists(path):
                _storage().delete(path)
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


def _signing_endpoint() -> str | None:
    """The S3 endpoint to sign a browser-bound URL against, if any.

    A SigV4 signature is bound to the host it was made for, so the URL is only
    usable if the browser reaches the bucket at that same host. Every
    self-hosted setup in docs/storage.md points MEDIA_BACKEND at an internal
    address (``minio:9000``, ``garage:3900``) and publishes media on a separate
    MEDIA_URL, so signing against the configured endpoint would hand out a
    link that resolves nowhere outside the container network.

    So: no endpoint configured at all means real AWS S3, whose public hostname
    is the one being signed, and a link works. A custom endpoint only produces
    a link when the operator names the public one, which is what
    MEDIA_BACKEND_S3_PUBLIC_ENDPOINT is for. Otherwise there is no usable
    link and the caller streams instead.
    """
    configured = getattr(settings, "AWS_S3_ENDPOINT_URL", "")
    public = settings.MEDIA_BACKEND_S3_PUBLIC_ENDPOINT
    if public:
        return public
    return None if configured else ""


def download_url(path: str, filename: str = "", content_type: str = "") -> str | None:
    """A URL the browser can fetch the file from directly, if there is one.

    None whenever the file is local, or the bucket is only reachable
    internally; the caller streams the file in that case.

    Deliberately not ``_storage().url()``: NeoDB always sets
    AWS_S3_CUSTOM_DOMAIN, and django-storages returns an unsigned public URL
    whenever a custom domain is set, which would defeat the point of storing
    these objects privately.

    The response headers are signed in too. A redirect otherwise loses the
    attachment disposition and the name, so a WordPress export would render in
    the browser instead of downloading, and every file would be saved under
    its UUID key.
    """
    if not is_stored(path) or not _on_s3():
        return None
    endpoint = _signing_endpoint()
    if endpoint is None:
        return None
    try:
        overrides = {}
        if filename:
            overrides["ResponseContentDisposition"] = (
                f"attachment; filename*=UTF-8''{quote(filename)}"
            )
        if content_type:
            overrides["ResponseContentType"] = content_type
        signer = S3Storage(
            custom_domain=None,
            querystring_auth=True,
            **({"endpoint_url": endpoint} if endpoint else {}),
        )
        return signer.url(path, parameters=overrides, expire=DOWNLOAD_URL_EXPIRY)
    except Exception as e:
        logger.warning(f"Failed to sign a download URL for {path}: {e}")
        return None
