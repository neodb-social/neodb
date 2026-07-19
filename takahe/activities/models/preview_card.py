from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from core.files import make_safe_client
from core.uris import ProxyAbsoluteUrl
from django.db import models
from django.utils import timezone
from stator.models import State, StateField, StateGraph, StatorModel

# ---------------------------------------------------------------------------
# Tracking param stripping
# ---------------------------------------------------------------------------

# Tier 1: domain allowlist — keep only these params for each host; strip rest.
_DOMAIN_ALLOWLIST: dict[str, set[str]] = {
    "mp.weixin.qq.com": {"__biz", "mid", "idx", "sn"},
    "weibo.com": set(),
    "www.xiaohongshu.com": set(),
    "xiaohongshu.com": set(),
    "www.tiktok.com": set(),
    "tiktok.com": set(),
    "www.douyin.com": set(),
    "douyin.com": set(),
}

# Tier 2: global blocklist — strip these param names from any URL not in tier 1.
_GLOBAL_BLOCKED_PARAMS: frozenset[str] = frozenset(
    [
        "fbclid",
        "gclid",
        "msclkid",
        "yclid",
        "ttclid",
        "twclid",
        "wbraid",
        "gbraid",
        "srsltid",
        "mkt_tok",
        "mc_eid",
        "igshid",
        "_ga",
        "_gl",
        "si",
        "t",
        "s",
        "trk",
        "ref",
        "source",
        "origin",
        "adid",
        "ad_id",
        "zanpid",
    ]
)

# Tier 2: strip any param whose name starts with one of these prefixes.
_GLOBAL_BLOCKED_PREFIXES: tuple[str, ...] = ("utm_", "ga_", "mtm_", "pk_")


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


def _parse_og_tags(html_text: str) -> dict[str, str]:
    """
    Minimal HTML parser extracting og:*, twitter:*, title, and meta description.
    Returns a flat dict of property/name → content.
    """
    result: dict[str, str] = {}
    title_capture = False
    title_buf: list[str] = []

    class _Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            nonlocal title_capture
            attr_dict = dict(attrs)
            if tag == "title":
                title_capture = True
            elif tag == "meta":
                prop = attr_dict.get("property") or attr_dict.get("name") or ""
                content = attr_dict.get("content") or ""
                if prop and content:
                    result[prop.lower()] = content

        def handle_endtag(self, tag):
            nonlocal title_capture
            if tag == "title":
                title_capture = False
                result["title"] = "".join(title_buf).strip()

        def handle_data(self, data):
            if title_capture:
                title_buf.append(data)

        def handle_error(self, message):
            pass  # tolerate malformed HTML

    try:
        _Parser().feed(html_text)
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class PreviewCardStates(StateGraph):
    needs_fetch = State(try_interval=3600)
    fetched = State()
    fetch_failed = State()

    needs_fetch.transitions_to(fetched)
    needs_fetch.transitions_to(fetch_failed)

    @classmethod
    def handle_needs_fetch(cls, instance: "PreviewCard"):
        """
        Fetches the URL, parses Open Graph tags, populates card fields.
        Returns fetched on success, fetch_failed on any error.
        """
        parsed = urlparse(instance.url)
        if parsed.scheme not in ("http", "https"):
            return cls.fetch_failed

        max_bytes = 2 * 1024 * 1024
        try:
            with make_safe_client() as client:
                with client.stream(
                    "GET", instance.url, follow_redirects=True
                ) as response:
                    if response.status_code >= 400:
                        return cls.fetch_failed
                    content_type = response.headers.get("content-type", "")
                    if "text/html" not in content_type:
                        return cls.fetch_failed
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        remaining = max_bytes - len(body)
                        if remaining <= 0:
                            break
                        body.extend(chunk[:remaining])
                        if len(body) >= max_bytes:
                            break
        except Exception:
            return cls.fetch_failed

        try:
            html_text = body.decode("utf-8", errors="replace")
        except Exception:
            return cls.fetch_failed

        meta = _parse_og_tags(html_text)

        instance.title = meta.get("og:title") or meta.get("title") or ""
        instance.description = (
            meta.get("og:description") or meta.get("description") or ""
        )
        # og:* values come from arbitrary remote HTML and can exceed the column
        # widths they're stored in. A truncated URL is useless, so drop an
        # oversized image_url (and its now-meaningless dimensions) rather than
        # store a broken value; clamp the text fields to fit. max_length is read
        # from the model so these stay in sync with the column definitions.
        instance.image_url = meta.get("og:image") or ""
        image_url_max = instance._meta.get_field("image_url").max_length
        if image_url_max and len(instance.image_url) > image_url_max:
            instance.image_url = ""
        try:
            instance.image_width = int(meta["og:image:width"])
        except KeyError, ValueError, TypeError:
            instance.image_width = None
        try:
            instance.image_height = int(meta["og:image:height"])
        except KeyError, ValueError, TypeError:
            instance.image_height = None
        if not instance.image_url:
            instance.image_width = None
            instance.image_height = None
        author_name_max = instance._meta.get_field("author_name").max_length
        instance.author_name = (meta.get("og:article:author") or "")[:author_name_max]
        provider_name_max = instance._meta.get_field("provider_name").max_length
        instance.provider_name = (parsed.hostname or "")[:provider_name_max]
        instance.provider_url = f"{parsed.scheme}://{parsed.netloc}"
        instance.fetched_at = timezone.now()
        instance.save(
            update_fields=[
                "title",
                "description",
                "image_url",
                "image_width",
                "image_height",
                "author_name",
                "provider_name",
                "provider_url",
                "fetched_at",
            ]
        )
        return cls.fetched


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class PreviewCard(StatorModel):
    """
    Stores Open Graph / link preview metadata for a URL found in a post.
    Keyed on the canonical (tracking-stripped) URL for deduplication.
    """

    class CardTypes(models.TextChoices):
        link = "link", "Link"
        photo = "photo", "Photo"
        video = "video", "Video"
        rich = "rich", "Rich"

    url = models.CharField(max_length=2048, unique=True, db_index=True)
    title = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    card_type = models.CharField(
        max_length=10,
        choices=CardTypes.choices,
        default=CardTypes.link,
    )
    author_name = models.CharField(max_length=500, blank=True, default="")
    author_url = models.CharField(max_length=2048, blank=True, default="")
    provider_name = models.CharField(max_length=500, blank=True, default="")
    provider_url = models.CharField(max_length=2048, blank=True, default="")
    embed_html = models.TextField(blank=True, default="")
    image_url = models.CharField(max_length=2048, blank=True, default="")
    image_width = models.IntegerField(null=True, blank=True)
    image_height = models.IntegerField(null=True, blank=True)
    blurhash = models.TextField(null=True, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    last_referenced_at = models.DateTimeField(null=True, blank=True, db_index=True)

    state = StateField(graph=PreviewCardStates)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    @staticmethod
    def strip_tracking_params(url: str) -> str:
        """
        Returns the canonical form of a URL with tracking parameters removed.

        Tier 1: Domain allowlist — for known social platforms, keep only the
        params that identify the content and strip everything else.

        Tier 2: Global blocklist — strip known tracking param names and any
        param whose name starts with utm_, ga_, mtm_, or pk_.
        """
        parsed = urlparse(url)
        host = parsed.hostname or ""

        # --- Tier 1: domain-specific allowlist ---
        if host in _DOMAIN_ALLOWLIST:
            allowed = _DOMAIN_ALLOWLIST[host]
            # Special case: Douyin discover?modal_id=X → /video/X
            if host in ("www.douyin.com", "douyin.com"):
                qs = parse_qs(parsed.query, keep_blank_values=False)
                if parsed.path.rstrip("/") in ("/discover", "") and "modal_id" in qs:
                    modal_id = qs["modal_id"][0]
                    return urlunparse(
                        parsed._replace(path=f"/video/{modal_id}", query="")
                    )
            new_qs = {
                k: v
                for k, v in parse_qs(parsed.query, keep_blank_values=False).items()
                if k in allowed
            }
            return urlunparse(parsed._replace(query=urlencode(new_qs, doseq=True)))

        # --- Tier 2: global blocklist ---
        qs = parse_qs(parsed.query, keep_blank_values=False)
        filtered = {
            k: v
            for k, v in qs.items()
            if k not in _GLOBAL_BLOCKED_PARAMS
            and not any(k.startswith(prefix) for prefix in _GLOBAL_BLOCKED_PREFIXES)
        }
        return urlunparse(parsed._replace(query=urlencode(filtered, doseq=True)))

    @property
    def image_proxy_url(self):
        if not self.image_url:
            return None
        return ProxyAbsoluteUrl(
            f"/proxy/preview_card/{self.pk}/",
            remote_url=self.image_url,
        )

    def to_mastodon_json(self) -> dict:
        image_proxy_url = self.image_proxy_url
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "type": self.card_type,
            "author_name": self.author_name,
            "author_url": self.author_url,
            "provider_name": self.provider_name,
            "provider_url": self.provider_url,
            "html": self.embed_html,
            "width": self.image_width or 0,
            "height": self.image_height or 0,
            "image": image_proxy_url.absolute if image_proxy_url else None,
            "embed_url": "",
            "blurhash": self.blurhash,
        }
