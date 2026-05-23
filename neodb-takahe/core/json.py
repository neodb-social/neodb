import json
from urllib.parse import urljoin

from httpx import Response

JSON_CONTENT_TYPES = [
    "application/ld+json",
    "application/activity+json",
]


def json_from_response(response: Response) -> dict:
    content_type, *parameters = (
        response.headers.get("Content-Type", "invalid").lower().split(";")
    )

    if content_type not in JSON_CONTENT_TYPES:
        raise ValueError(f"Invalid content type: {content_type}")

    charset = None

    for parameter in parameters:
        if "=" not in parameter:
            continue
        key, value = parameter.split("=")
        if key.strip() == "charset":
            charset = value.strip()

    if charset:
        return json.loads(response.content.decode(charset))
    else:
        # if no charset informed, default to
        # httpx json for encoding inference
        return response.json()


def _split_link_entries(value: str) -> list[str]:
    """Split an RFC 8288 ``Link`` header on top-level commas."""
    entries: list[str] = []
    depth = 0
    in_quote = False
    start = 0
    i = 0
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


def _parse_link_entry(entry: str) -> tuple[str, dict[str, str]] | None:
    entry = entry.strip()
    if not entry.startswith("<"):
        return None
    try:
        end = entry.index(">")
    except ValueError:
        return None
    url = entry[1:end].strip()
    params: dict[str, str] = {}
    for piece in entry[end + 1 :].split(";"):
        piece = piece.strip()
        if "=" not in piece:
            continue
        k, _, v = piece.partition("=")
        params[k.strip().lower()] = v.strip().strip('"')
    return url, params


def find_ap_alternate(response: Response) -> str | None:
    """Return an alternate ActivityPub URL advertised by the response.

    Some sites -- notably WordPress's ActivityPub plugin -- do not
    content-negotiate the canonical permalink and always return HTML. They
    instead advertise the AP object URL via an HTTP ``Link`` header
    (``rel="alternate"; type="application/activity+json"``) or the equivalent
    ``<link>`` tag in the HTML ``<head>``. Mastodon's ``FetchResourceService``
    follows the same hints; mirroring that behaviour here lets the fetcher
    resolve such posts.
    """
    headers = response.headers
    raw_links: list[str] = []
    try:
        raw_links = list(headers.get_list("link"))  # type: ignore[attr-defined]
    except AttributeError:
        single = headers.get("link")
        if single:
            raw_links = [single]
    for header_value in raw_links:
        for entry in _split_link_entries(header_value):
            parsed = _parse_link_entry(entry)
            if not parsed:
                continue
            url, params = parsed
            rels = params.get("rel", "").split()
            link_type = params.get("type", "").split(";", 1)[0].strip().lower()
            if "alternate" in rels and link_type in JSON_CONTENT_TYPES:
                return urljoin(str(response.url), url)

    content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if "html" in content_type:
        try:
            from lxml import html as lxml_html
        except ImportError:
            return None
        try:
            doc = lxml_html.fromstring(response.content)
        except Exception:
            return None
        for link in doc.iter("link"):
            rel = (link.get("rel") or "").lower().split()
            link_type = (link.get("type") or "").split(";", 1)[0].strip().lower()
            href = link.get("href")
            if "alternate" in rel and link_type in JSON_CONTENT_TYPES and href:
                return urljoin(str(response.url), href)
    return None
