import re
import uuid
from typing import Any

import dateparser
from django.utils import timezone

from common.models import (
    coerce_video_duration,
    earliest_partial_date,
    normalize_countries,
    parse_duration_text,
    parse_partial_date,
)

from .common import IdType


def check_digit_10(isbn):
    assert len(isbn) == 9
    sum = 0
    for i in range(len(isbn)):
        c = int(isbn[i])
        w = i + 1
        sum += w * c
    r = sum % 11
    return "X" if r == 10 else str(r)


def check_digit_13(isbn):
    assert len(isbn) == 12
    sum = 0
    for i in range(len(isbn)):
        c = int(isbn[i])
        w = 3 if i % 2 else 1
        sum += w * c
    r = 10 - (sum % 10)
    return "0" if r == 10 else str(r)


def isbn_10_to_13(isbn) -> str | None:
    if not isbn or len(isbn) != 10:
        return None
    return "978" + isbn[:-1] + check_digit_13("978" + isbn[:-1])


def isbn_13_to_10(isbn):
    if not isbn or len(isbn) != 13 or isbn[:3] != "978":
        return None
    else:
        return isbn[3:12] + check_digit_10(isbn[3:12])


def is_isbn_13(isbn):
    return re.match(r"^\d{13}$", isbn) is not None


def is_isbn_10(isbn):
    return re.match(r"^\d{9}[X0-9]$", isbn) is not None


def is_asin(asin):
    return re.match(r"^B[A-Z0-9]{9}$", asin) is not None


def detect_isbn_asin(s: str) -> tuple[IdType, str] | tuple[None, None]:
    if not s:
        return None, None
    n = re.sub(r"[^0-9A-Z]", "", s.upper())
    if is_isbn_13(n) and check_digit_13(n[:-1]) == n[-1]:
        return IdType.ISBN, n
    if is_isbn_10(n) and check_digit_10(n[:-1]) == n[-1]:
        v = isbn_10_to_13(n)
        return (IdType.ISBN, v) if v else (None, None)
    if is_asin(n):
        return IdType.ASIN, n
    return None, None


def binding_to_format(binding: str | None):
    from .book import Edition

    if not binding:
        return None
    if re.search(r"(Audio|Audible|音频)", binding, flags=re.IGNORECASE):
        return Edition.BookFormat.AUDIOBOOK
    if re.search(
        r"(pub|ebook|e-book|kindle|electronic|电子)", binding, flags=re.IGNORECASE
    ):
        return Edition.BookFormat.EBOOK
    if re.search(r"(web|网)", binding, flags=re.IGNORECASE):
        return Edition.BookFormat.WEB
    if re.search(r"(精|Hard)", binding, flags=re.IGNORECASE):
        return Edition.BookFormat.HARDCOVER
    if re.search(r"(平|Paper|Soft)", binding, flags=re.IGNORECASE):
        return Edition.BookFormat.PAPERBACK
    return None


def upc_to_gtin_13(upc: str):
    """
    Convert UPC-A to GTIN-13, return None if validation failed

    may add or remove padding 0s from different source
    """
    s = upc.strip() if upc else ""
    if not re.match(r"^\d+$", s):
        return None
    if len(s) < 13:
        s = s.zfill(13)
    elif len(s) > 13:
        if re.match(r"^0+$", s[0 : len(s) - 13]):
            s = s[len(s) - 13 :]
        else:
            return None
    return s


def canonicalize_release_date_key(metadata: dict[str, Any]) -> None:
    """Re-canonicalize metadata["release_date"] into partial ISO form.

    Falls back to dateparser for free-text dates (e.g. localized Steam
    strings). Unparseable non-empty strings are kept as-is rather than
    destroyed.
    """
    rd = metadata.get("release_date")
    if rd is None:
        return
    p = parse_partial_date(rd)
    if p is None and isinstance(rd, str) and rd.strip():
        dp = dateparser.parse(rd.strip())
        p = dp.date().isoformat() if dp else None
    if p:
        metadata["release_date"] = p
    elif not isinstance(rd, str) or not rd.strip():
        metadata.pop("release_date", None)


def _coerce_duration_key(metadata: dict[str, Any], key: str, legacy: bool) -> None:
    v = metadata.get(key)
    if v is None:
        return
    if isinstance(v, str):
        parsed = parse_duration_text(v)
    elif isinstance(v, (int, float)):
        # the int-means-minutes inference only applies to legacy-shaped
        # metadata; new-shape values are already seconds
        parsed = coerce_video_duration(v) if legacy else (int(v) or None)
    else:
        parsed = None
    if parsed and parsed > 0:
        metadata[key] = parsed
    else:
        metadata.pop(key, None)


def normalize_legacy_video_metadata(metadata: dict[str, Any]) -> None:
    """Translate legacy Movie/TVShow/TVSeason metadata shapes in place.

    Sources: federated peers running older code, ndjson restores, and
    local rows that predate the unification:
    - duration / single_episode_length free text or minutes -> seconds
    - area (region names) -> origin_country (ISO 3166-1 alpha-2)
    - showtime [{time, region}] -> release_date (earliest); year fallback

    Legacy shape is detected by the presence of pre-unification keys
    (and the absence of the current "length" key), so running this on
    already-converted metadata is a no-op.
    """
    legacy = "length" not in metadata and any(
        k in metadata for k in ("area", "showtime", "year")
    )
    # current peers emit canonical seconds under "length"; the legacy
    # "duration" shape is only used when length is absent
    duration = metadata.pop("duration", None)
    if not metadata.get("length") and duration is not None:
        if isinstance(duration, str):
            length = parse_duration_text(duration)
        elif isinstance(duration, (int, float)):
            length = coerce_video_duration(duration) if legacy else int(duration)
        else:
            length = None
        if length and length > 0:
            metadata["length"] = length
    _coerce_duration_key(metadata, "single_episode_length", legacy)
    area = metadata.pop("area", None)
    if area and not metadata.get("origin_country"):
        if isinstance(area, str):
            area = [area]
        if isinstance(area, list):
            metadata["origin_country"] = normalize_countries(
                [a for a in area if isinstance(a, str)]
            )
    showtime = metadata.pop("showtime", None)
    if not metadata.get("release_date") and isinstance(showtime, list):
        rd = earliest_partial_date(
            t.get("time") for t in showtime if isinstance(t, dict)
        )
        if rd:
            metadata["release_date"] = rd
        else:
            # nothing parseable; keep the original data around
            metadata["showtime"] = showtime
    year = metadata.pop("year", None)
    if year and not metadata.get("release_date"):
        rd = parse_partial_date(year if isinstance(year, int) else str(year))
        if rd:
            metadata["release_date"] = rd
    canonicalize_release_date_key(metadata)


def resource_cover_path(resource, filename):
    fn = (
        timezone.now().strftime("%Y/%m/%d/")
        + str(uuid.uuid4())
        + "."
        + filename.split(".")[-1]
    )
    return "item/" + resource.id_type + "/" + fn


def item_cover_path(item, filename):
    fn = (
        timezone.now().strftime("%Y/%m/%d/")
        + str(uuid.uuid4())
        + "."
        + filename.split(".")[-1]
    )
    return "item/" + item.category + "/" + fn


def piece_cover_path(item, filename):
    fn = (
        timezone.now().strftime("%Y/%m/%d/")
        + str(uuid.uuid4())
        + "."
        + filename.split(".")[-1]
    )
    return f"user/{item.owner_id or '_'}/{fn}"
