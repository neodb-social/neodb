import io
import ipaddress
import socket

from blurhash_rs import blurhash_encode
import httpx
from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# SSRF protection -- shared across all outbound HTTP paths
# ---------------------------------------------------------------------------


class SSRFAttemptError(ValueError):
    pass


def check_url_safety(request: httpx.Request) -> None:
    """
    httpx event hook that blocks requests to private/reserved IP addresses.

    Attach to an httpx.Client via ``event_hooks={"request": [check_url_safety]}``.
    The hook fires on every request including redirect hops, preventing DNS
    rebinding attacks.  Raises :class:`SSRFAttemptError` to abort the request.
    """
    host = request.url.host
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpx.ConnectError(f"Cannot resolve host {host!r}: {exc}") from exc
    for _, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise SSRFAttemptError(
                f"Request to {host!r} blocked: resolved to non-global IP {sockaddr[0]}"
            )


def make_safe_client(**kwargs) -> httpx.Client:
    """
    Return an ``httpx.Client`` with SSRF protection, sensible timeouts, and
    a project User-Agent header.  Extra *kwargs* are forwarded to
    ``httpx.Client`` and can override any default.
    """
    defaults: dict = {
        "follow_redirects": True,
        "max_redirects": 5,
        "timeout": httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        "headers": {"User-Agent": settings.TAKAHE_USER_AGENT},
        "event_hooks": {"request": [check_url_safety]},
    }
    defaults.update(kwargs)
    return httpx.Client(**defaults)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


class ImageFile(File):
    image: Image


def resize_image(
    image: File,
    *,
    size: tuple[int, int],
    cover=True,
    keep_format=False,
) -> ImageFile:
    """
    Resizes an image to fit insize the given size (cropping one dimension
    to fit if needed)
    """
    with Image.open(image) as img:
        try:
            # Take any orientation EXIF data, apply it, and strip the
            # orientation data from the new image.
            img = ImageOps.exif_transpose(img)
        except Exception:  # noqa
            # exif_transpose can crash with different errors depending on
            # the EXIF keys. Just ignore them all, better to have a rotated
            # image than no image.
            pass

        if cover:
            resized_image = ImageOps.fit(img, size, method=Image.Resampling.BILINEAR)
        else:
            resized_image = img.copy()
            resized_image.thumbnail(size, resample=Image.Resampling.BILINEAR)
        new_image_bytes = io.BytesIO()
        if keep_format:
            resized_image.save(new_image_bytes, format=img.format)
            file = ImageFile(new_image_bytes)
        else:
            resized_image.save(new_image_bytes, format="webp", save_all=True)
            file = ImageFile(new_image_bytes, name="image.webp")
        file.image = resized_image
        return file


def blurhash_image(file) -> str:
    """
    Returns the blurhash for an image
    """
    return blurhash_encode(file, x_components=4, y_components=4)


def get_remote_file(
    url: str,
    *,
    timeout: float = settings.SETUP.REMOTE_TIMEOUT,
    max_size: int | None = None,
) -> tuple[File | None, str | None]:
    """
    Download a URL and return the File and content-type.
    """
    with make_safe_client(timeout=timeout) as client:
        with client.stream("GET", url, follow_redirects=True) as stream:
            allow_download = max_size is None
            if max_size:
                try:
                    content_length = int(stream.headers["content-length"])
                    allow_download = content_length <= max_size
                except KeyError, TypeError:
                    pass
            if allow_download:
                file = ContentFile(stream.read(), name=url)
                return file, stream.headers.get(
                    "content-type", "application/octet-stream"
                )

    return None, None
