from urllib.parse import urlparse

import httpx
from activities.models import Emoji, PostAttachment
from core.files import SSRFAttemptError, check_url_safety, make_safe_client
from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.templatetags.static import static
from django.views.generic import View

from users.models import Identity


HOST_VERDICT_TTL = 300
HOST_UNRESOLVED_TTL = 60
HOST_UNRESOLVED = "nores"


def public_http_url_verdict(url: str) -> bool | None:
    """
    Whether url is http(s) on a host that resolves only to global addresses:
    True if so, False if not, None if the host does not resolve right now.

    nginx fetches accelerated URLs itself, out of reach of the httpx hook, so
    the view has to vet them before handing them over. nginx never caches the
    accel redirect, so this runs on every proxy request; the verdict per host
    and port is memoized for a short while to avoid a DNS lookup each time.
    """
    try:
        request = httpx.Request("GET", url)
        if request.url.scheme not in ("http", "https") or not request.url.host:
            return False
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        key = f"mediaproxy:host_ok:{request.url.host}:{port}"
        cached = cache.get(key)
        if isinstance(cached, bool):
            return cached
        if cached == HOST_UNRESOLVED:
            return None
        try:
            check_url_safety(request)
        except SSRFAttemptError:
            verdict = False
        except httpx.ConnectError:
            cache.set(key, HOST_UNRESOLVED, HOST_UNRESOLVED_TTL)
            return None
        else:
            verdict = True
        cache.set(key, verdict, HOST_VERDICT_TTL)
        return verdict
    except httpx.InvalidURL, UnicodeError:
        return False


def transient_failure_headers() -> dict[str, str]:
    # keeps nginx's proxy cache and browsers from pinning a passing failure
    return {"Cache-Control": "no-store", "X-Accel-Expires": "0"}


class BaseProxyView(View):
    """
    Base class for proxying remote content.
    """

    def get(self, request, **kwargs):
        self.kwargs = kwargs
        remote_url = self.get_remote_url()
        try:
            scheme = urlparse(remote_url or "").scheme
        except ValueError:
            raise Http404()
        if scheme not in ("http", "https"):
            raise Http404()
        # See if we can do the nginx trick or a normal forward
        if request.headers.get("x-takahe-accel") and not request.GET.get("no_accel"):
            verdict = public_http_url_verdict(remote_url)
            if verdict is None:
                return HttpResponse(status=503, headers=transient_failure_headers())
            if not verdict:
                raise Http404()
            bits = urlparse(remote_url)
            redirect_url = (
                f"/__takahe_accel__/{bits.scheme}/{bits.hostname}/{bits.path}"
            )
            if bits.query:
                redirect_url += f"?{bits.query}"
            return HttpResponse(
                "",
                headers={
                    "X-Accel-Redirect": "/__takahe_accel__/",
                    "X-Takahe-RealUri": remote_url,
                    # No Cache-Control here: nginx copies it through the
                    # accel redirect onto every response including error
                    # passthroughs; the client TTL is added by nginx instead
                },
            )
        else:
            max_bytes = settings.SETUP.MEDIA_MAX_IMAGE_FILESIZE_MB * 1024 * 1024
            try:
                with make_safe_client(
                    timeout=settings.SETUP.REMOTE_TIMEOUT,
                ) as client:
                    with client.stream("GET", remote_url) as remote_response:
                        if remote_response.status_code >= 400:
                            return HttpResponse(status=502)
                        # Only serve content whose Content-Type is on the image
                        # allowlist.  A malicious remote server could set
                        # text/html and turn the proxy into an XSS vector on
                        # the local domain.
                        content_type = remote_response.headers.get(
                            "Content-Type", "application/octet-stream"
                        )
                        if not content_type.startswith("image/"):
                            content_type = "application/octet-stream"
                        cache_control = remote_response.headers.get(
                            "Cache-Control", "public, max-age=3600"
                        )
                        body = bytearray()
                        for chunk in remote_response.iter_bytes(chunk_size=65536):
                            remaining = max_bytes - len(body)
                            if remaining <= 0:
                                return HttpResponse(status=502)
                            body.extend(chunk[:remaining])
                            if len(chunk) > remaining:
                                return HttpResponse(status=502)
            except httpx.RequestError, SSRFAttemptError:
                return HttpResponse(status=502)
            return HttpResponse(
                bytes(body),
                headers={
                    "Content-Type": content_type,
                    "Cache-Control": cache_control,
                },
            )

    def get_remote_url(self) -> str:
        raise NotImplementedError()


class EmojiCacheView(BaseProxyView):
    """
    Proxies Emoji
    """

    def get_remote_url(self):
        self.emoji = get_object_or_404(Emoji, pk=self.kwargs["emoji_id"])

        if not self.emoji.remote_url:
            raise Http404()
        return self.emoji.remote_url


class IdentityIconCacheView(BaseProxyView):
    """
    Proxies identity icons (avatars).

    Falls back to the default avatar image instead of returning an error when
    the icon is unavailable, so callers never surface a broken avatar image.
    """

    #: Static path (within takahe) of the placeholder avatar.
    default_icon_static_path = "img/avatar.png"

    def get(self, request, **kwargs):
        try:
            response = super().get(request, **kwargs)
        except Http404:
            # No such identity, a local identity, or no stored icon_uri.
            return self.default_icon_response()
        # The icon host does not resolve right now: show the placeholder, but
        # do not let it stand in for the icon once the host is back.
        if response.status_code == 503:
            return self.default_icon_response(transient=True)
        # Remote fetch failed (>= 400, too large, or network/SSRF error).
        if response.status_code >= 400:
            return self.default_icon_response()
        return response

    def default_icon_response(self, transient: bool = False) -> HttpResponse:
        # static() honours STATIC_URL and manifest hashing so the redirect
        # points at the file collectstatic actually serves.
        response = redirect(static(self.default_icon_static_path))
        if transient:
            for name, value in transient_failure_headers().items():
                response.headers[name] = value
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    def get_remote_url(self) -> str:
        self.identity = get_object_or_404(Identity, pk=self.kwargs["identity_id"])
        if self.identity.local or not self.identity.icon_uri:
            raise Http404()
        return self.identity.icon_uri


class IdentityImageCacheView(BaseProxyView):
    """
    Proxies identity profile header images
    """

    def get_remote_url(self):
        self.identity = get_object_or_404(Identity, pk=self.kwargs["identity_id"])
        if self.identity.local or not self.identity.image_uri:
            raise Http404()
        return self.identity.image_uri


class PostAttachmentCacheView(BaseProxyView):
    """
    Proxies post media (images only, videos should always be offloaded to remote)
    """

    def get_remote_url(self):
        self.post_attachment = get_object_or_404(
            PostAttachment, pk=self.kwargs["attachment_id"]
        )
        if not self.post_attachment.is_image():
            raise Http404()
        return self.post_attachment.remote_url


class PreviewCardImageCacheView(BaseProxyView):
    """
    Proxies preview card images (og:image remote URLs).
    """

    def get_remote_url(self):
        from activities.models import PreviewCard

        card = get_object_or_404(PreviewCard, pk=self.kwargs["card_id"])
        if not card.image_url:
            raise Http404()
        return card.image_url
