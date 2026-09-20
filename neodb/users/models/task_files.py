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

#: Read an object larger than this to disk rather than holding it in the
#: worker's memory. Import archives run to hundreds of megabytes.
SPOOL_TO_DISK_ABOVE = 16 * 1024 * 1024


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
    only tightened when one is in use at all. An ACL cannot override a bucket
    policy that already grants anonymous reads across the whole bucket, nor
    Garage's website endpoint, which serves a whole bucket once it is allowed.
    docs/storage.md says how to keep these two prefixes out of such a policy,
    and MEDIA_BACKEND_S3_TASK_BUCKET puts them in a bucket of their own where
    that is not possible.

    ``max_memory_size`` matters more than it looks: django-storages reads an
    object into a SpooledTemporaryFile, and the default of 0 means the spool
    never rolls over, so a whole import archive would sit in the worker's
    memory. A threshold keeps anything bigger on disk.
    """
    return S3Storage(**_s3_task_options())


def _s3_task_options() -> dict[str, Any]:
    """The settings shared by the task storage and the signer.

    Shared rather than repeated because the two must agree on where the file
    is: a signer left on the media bucket would sign a URL for an object that
    was uploaded to the task bucket, and since signing still succeeds the
    streaming fallback would not catch it.
    """
    options: dict[str, Any] = {
        "default_acl": "private" if settings.MEDIA_BACKEND_S3_ACL else None,
        "querystring_auth": True,
        "max_memory_size": SPOOL_TO_DISK_ABOVE,
    }
    if settings.MEDIA_BACKEND_S3_TASK_BUCKET:
        options["bucket_name"] = settings.MEDIA_BACKEND_S3_TASK_BUCKET
    return options


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


def save_local_file(source: str, filename: str, path_root: str) -> str:
    """Store a file already written to local disk, returning its storage key."""
    with open(source, "rb") as f:
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
    local = local_path(path)
    if local is not None:
        directory = os.path.dirname(local) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp, local)
        except Exception:
            discard(tmp)
            raise
        return
    _storage().save(path, ContentFile(content))


def local_path(path: str) -> str | None:
    """The filesystem path behind ``path``, when the storage has one.

    None only for a remote backend. A key on the local backend resolves to a
    real file, which callers want for two reasons: rewriting it through
    ``Storage.save`` would not overwrite, since FileSystemStorage picks a
    suffixed name and leaves every recorded reference on the stale file; and
    reading it needs no staging copy, which for an import archive would mean
    a second copy of the whole thing on the same disk.
    """
    if not is_stored(path):
        return path
    try:
        return _storage().path(path)
    except NotImplementedError:
        return None


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
    is staged to a temp file for the duration rather than streamed. A local
    backend hands back the real file, with nothing copied and nothing to
    clean up; callers only read through this, and write through
    ``overwrite_task_file``.
    """
    direct = local_path(path)
    if direct is not None:
        yield direct
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
            **_s3_task_options(),
            custom_domain=None,
            # pinned, because botocore still resolves some regions to the
            # v2 scheme for a presigned URL, which a modern bucket rejects
            signature_version="s3v4",
            **({"endpoint_url": endpoint} if endpoint else {}),
        )
        return signer.url(path, parameters=overrides, expire=DOWNLOAD_URL_EXPIRY)
    except Exception as e:
        logger.warning(f"Failed to sign a download URL for {path}: {e}")
        return None
