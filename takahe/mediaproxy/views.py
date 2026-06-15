from urllib.parse import urlparse

import httpx
from activities.models import Emoji, PostAttachment
from core.files import SSRFAttemptError, make_safe_client
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.generic import View

from users.models import Identity


class BaseProxyView(View):
    """
    Base class for proxying remote content.
    """

    def get(self, request, **kwargs):
        self.kwargs = kwargs
        remote_url = self.get_remote_url()
        # See if we can do the nginx trick or a normal forward
        if request.headers.get("x-takahe-accel") and not request.GET.get("no_accel"):
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
                    "Cache-Control": "public",
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
    Proxies identity icons (avatars)
    """

    def get_remote_url(self):
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
