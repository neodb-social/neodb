from time import sleep
from urllib.parse import urljoin

import httpx

from catalog.sites.fedi import FediverseInstance
from common.management.base import CommandError, SiteCommand
from takahe.models import Identity, InboxMessage, Post

actor_types = ["person", "service", "application", "group", "organization"]
post_types = ["note", "article", "post", "question", "event", "video", "audio", "image"]

# Strict set used for ``Link: rel="alternate"`` discovery. ``application/json``
# is deliberately excluded -- a bare-JSON alternate is too broad to safely
# auto-follow as ActivityPub.
JSON_AP_TYPES = ("application/activity+json", "application/ld+json")
# Broader set we are willing to parse as the response body once content is
# in hand (matches the existing handle() behaviour).
JSON_MEDIA_TYPES = ("application/json",) + JSON_AP_TYPES


def _split_link_entries(value: str) -> list[str]:
    entries: list[str] = []
    depth = 0
    in_quote = False
    start = i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
        elif not in_quote:
            if c == "<":
                depth += 1
            elif c == ">":
                depth = max(0, depth - 1)
            elif c == "," and depth == 0:
                entries.append(value[start:i])
                start = i + 1
        i += 1
    entries.append(value[start:])
    return entries


def find_ap_alternate_url(response: httpx.Response) -> str | None:
    """Return an alternate ActivityPub URL advertised by ``response``.

    Mirrors the helper in ``neodb-takahe/core/json.py``: looks at both the
    HTTP ``Link`` header (``rel="alternate"; type="application/activity+json"``)
    and -- when the body is HTML -- the equivalent ``<link>`` tag in the
    document head. WordPress's ActivityPub plugin (and similar hosts) do
    not content-negotiate the canonical permalink; the AP object is only
    reachable through one of these hints.
    """
    base = str(response.url)
    for header_value in response.headers.get_list("link"):
        for entry in _split_link_entries(header_value):
            entry = entry.strip()
            if not entry.startswith("<"):
                continue
            try:
                end = entry.index(">")
            except ValueError:
                continue
            url = entry[1:end].strip()
            params: dict[str, str] = {}
            for piece in entry[end + 1 :].split(";"):
                piece = piece.strip()
                if "=" not in piece:
                    continue
                k, _, v = piece.partition("=")
                params[k.strip().lower()] = v.strip().strip('"')
            rels = params.get("rel", "").split()
            link_type = params.get("type", "").split(";", 1)[0].strip().lower()
            if "alternate" in rels and link_type in JSON_AP_TYPES:
                return urljoin(base, url)

    content_type = (
        response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    )
    if "html" in content_type:
        from lxml import html as lxml_html

        try:
            doc = lxml_html.fromstring(response.content)
        except Exception:
            return None
        for link in doc.iter("link"):
            rels = (link.get("rel") or "").lower().split()
            link_type = (link.get("type") or "").split(";", 1)[0].strip().lower()
            href = link.get("href")
            if "alternate" in rels and link_type in JSON_AP_TYPES and href:
                return urljoin(base, href)
    return None


class Command(SiteCommand):
    help = "Fetch a post from a URL"

    def add_arguments(self, parser):
        parser.add_argument(
            "url",
            type=str,
            help="URL of the post to fetch",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=30,
            help="Timeout in seconds for fetching operation (default: 30)",
        )

    def handle(self, *args, **options):
        url = options["url"]
        timeout = options["timeout"]
        self.stdout.write(f"Fetching post from URL: {url}")
        try:
            headers = {
                "Accept": "application/json,application/activity+json,application/ld+json"
            }
            response = httpx.get(
                url, headers=headers, timeout=timeout, follow_redirects=True
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            self.stdout.write(f"Content-Type: {content_type}")
            # RFC 7231: parameter values (charset, etc.) are case-insensitive.
            # write.as serves ``application/activity+json; charset=UTF-8`` and
            # the prior endswith("json; charset=utf-8") miss made the fetcher
            # silently bail with "Content type is not JSON".
            bare_media_type = content_type.split(";", 1)[0].strip().lower()
            # WordPress's ActivityPub plugin (and similar hosts) serve HTML
            # on permalink URLs and advertise the AP object via
            # ``Link: rel="alternate"; type="application/activity+json"`` or
            # the equivalent ``<link>`` tag. Follow it once before giving up.
            if bare_media_type not in JSON_MEDIA_TYPES:
                alt = find_ap_alternate_url(response)
                if alt:
                    self.stdout.write(f"Following AP alternate: {alt}")
                    response = httpx.get(
                        alt, headers=headers, timeout=timeout, follow_redirects=True
                    )
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    self.stdout.write(f"Content-Type: {content_type}")
                    bare_media_type = content_type.split(";", 1)[0].strip().lower()
            if bare_media_type in JSON_MEDIA_TYPES:
                j = response.json()
                typ = j.get("type", "").lower()
                uri = j.get("id", "")
                if not typ or not uri:
                    self.stdout.write(self.style.WARNING("Unknown object id/type"))
                elif typ in actor_types:
                    InboxMessage.create_internal({"type": "searchurl", "url": url})
                    self.stdout.write("Fetching Takahe identity", ending="")
                    tries = timeout
                    while tries > 0:
                        self.stdout.write(".", ending="")
                        tries -= 1
                        i = Identity.objects.filter(actor_uri=uri).first()
                        if i:
                            self.stdout.write(
                                self.style.SUCCESS(f"\nIdentity fetched: @{i.handle}")
                            )
                            break
                        sleep(1)
                        if tries == 0:
                            self.stdout.write(self.style.ERROR("timeout"))
                elif typ in post_types:
                    InboxMessage.create_internal({"type": "searchurl", "url": url})
                    self.stdout.write("Fetching Takahe post", ending="")
                    tries = timeout
                    while tries > 0:
                        self.stdout.write(".", ending="")
                        tries -= 1
                        p = Post.objects.filter(object_uri=uri).first()
                        if p:
                            self.stdout.write(
                                self.style.SUCCESS(f"\nPost fetched: {p}\n{p.content}")
                            )
                            break
                        sleep(1)
                        if tries == 0:
                            self.stdout.write(self.style.ERROR("timeout"))
                else:
                    s = FediverseInstance(url=url)
                    r = s.get_resource_ready()
                    if r:
                        self.stdout.write(
                            self.style.SUCCESS(f"NeoDB resource is ready: {r.metadata}")
                        )
            else:
                self.stdout.write(
                    self.style.WARNING(f"Content type is not JSON: {content_type}")
                )
        except httpx.RequestError as e:
            raise CommandError(f"Request error: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise CommandError(f"HTTP error: {e.response.status_code} {str(e)}")
