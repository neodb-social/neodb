import hashlib
import sys
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.utils.crypto import constant_time_compare, salted_hmac


class RelativeAbsoluteUrl:
    """
    Represents a URL that can have both "relative" and "absolute" forms
    for various use either locally or remotely.
    """

    absolute: str
    relative: str

    def __init__(self, absolute: str, relative: str | None = None):
        if "://" not in absolute:
            raise ValueError(f"Absolute URL {absolute!r} is not absolute!")
        self.absolute = absolute
        self.relative = relative or absolute


class AutoAbsoluteUrl(RelativeAbsoluteUrl):
    """
    Automatically makes the absolute variant by using either settings.MAIN_DOMAIN
    or a passed identity's URI domain.
    """

    def __init__(
        self,
        relative: str,
        identity=None,
    ):
        self.relative = relative
        if identity:
            absolute_prefix = f"https://{identity.domain.uri_domain}/"
        else:
            absolute_prefix = f"https://{settings.MAIN_DOMAIN}/"
        self.absolute = urljoin(absolute_prefix, self.relative)


class ProxyAbsoluteUrl(AutoAbsoluteUrl):
    """
    AutoAbsoluteUrl variant for proxy paths, that also attaches a remote URI hash
    plus extension to the end if it can.
    """

    def __init__(
        self,
        relative: str,
        identity=None,
        remote_url: str | None = None,
        capability: str | None = None,
    ):
        """
        `capability`, when given, takes the place of the remote URL hash: the
        proxy view requires it for media that is not public.
        """
        if remote_url:
            # See if there is a file extension we can grab
            extension = "bin"
            remote_filename = remote_url.split("/")[-1]
            if "." in remote_filename:
                candidate = remote_filename.split(".")[-1]
                # the extension lands in hrefs and inline styles, so keep
                # only a plain ASCII word
                if candidate.isascii() and candidate.isalnum() and len(candidate) <= 8:
                    extension = candidate
            # When provided, attach a hash of the remote URL
            # SHA1 chosen as it generally has the best performance in modern python, and security is not a concern
            # Hash truncation is generally fine, as in the typical use case the hash is scoped to the identity PK.
            token = (
                capability or hashlib.sha1(remote_url.encode("utf-8")).hexdigest()[:10]
            )
            relative += f"{token}.{extension}"
        super().__init__(relative, identity)


def proxy_capability(kind: str, pk: int | str, secret: str | None = None) -> str:
    """
    Unguessable path segment that authorizes fetching proxied media `pk`.

    Proxy URLs are fetched without credentials, by API clients and by <img>
    tags alike, so access to non-public media rides on the URL itself.
    """
    return salted_hmac(
        f"takahe.proxy.{kind}", str(pk), secret=secret, algorithm="sha256"
    ).hexdigest()[:32]


def proxy_capability_valid(kind: str, pk: int | str, value: str | None) -> bool:
    """
    Whether `value`, a proxy URL's last path segment with or without its
    extension, is the capability for `pk` under the current or a retired key.
    """
    token = (value or "").split(".", 1)[0]
    if not token:
        return False
    return any(
        constant_time_compare(token, proxy_capability(kind, pk, secret))
        for secret in (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS)
    )


class StaticAbsoluteUrl(RelativeAbsoluteUrl):
    """
    Creates static URLs given only the static-relative path
    """

    def __init__(self, path: str):
        try:
            static_url = staticfiles_storage.url(path)
        except ValueError:
            # Suppress static issues during the first collectstatic
            # Yes, I know it's a big hack! Pull requests welcome :)
            if "collectstatic" in sys.argv:
                super().__init__("https://example.com/")
                return
            raise
        if "://" in static_url:
            super().__init__(static_url)
        else:
            super().__init__(
                urljoin(f"https://{settings.MAIN_DOMAIN}/", static_url), static_url
            )
