"""
Duration support

Catalog durations are stored as integer seconds. Helpers here parse the
free-text and mixed-unit shapes found in scraped data and legacy
metadata, and format seconds for display ("2h 14m").
"""

import re

from django.utils.dateparse import parse_duration as _django_parse_duration

_RE_COLON = re.compile(r"^(\d+):(\d{1,2})(?::(\d{1,2}))?$")
_RE_UNITS = re.compile(
    r"(?:(\d+)\s*(?:h(?:ou)?rs?|h|小时|小時|时|時))?\s*"
    r"(?:(\d+)\s*(?:min(?:ute)?s?|m|分钟|分鐘|分))?\s*"
    r"(?:(\d+)\s*(?:sec(?:ond)?s?|s|秒))?",
    re.IGNORECASE,
)


def parse_duration_text(value: str) -> int | None:
    """Parse a human duration string into seconds.

    Handles "148分钟", "2小时28分钟", "148 min", "2h 14m", "1:30:00",
    "90:00" (M:SS), ISO-8601 "PT2H28M", and bare digits (minutes, the
    Douban/TMDB convention). Returns None when nothing parses.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s) * 60 or None
    if s.upper().startswith(("P", "PT")):
        td = _django_parse_duration(s)
        if td is not None:
            return int(td.total_seconds()) or None
    m = _RE_COLON.match(s)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if c is not None:  # H:MM:SS
            return int(a) * 3600 + int(b) * 60 + int(c) or None
        return int(a) * 60 + int(b) or None  # M:SS
    m = _RE_UNITS.fullmatch(s)
    if m and any(m.groups()):
        h, mi, sec = (int(g) if g else 0 for g in m.groups())
        return h * 3600 + mi * 60 + sec or None
    # units regex is anchored; retry unanchored for strings with trailing
    # noise like "148分钟(导演剪辑版)"
    m = _RE_UNITS.match(s)
    if m and any(m.groups()):
        h, mi, sec = (int(g) if g else 0 for g in m.groups())
        return h * 3600 + mi * 60 + sec or None
    return None


def coerce_video_duration(value: str | int | float | None) -> int | None:
    """Coerce a legacy Movie/TV duration value into seconds.

    Strings are parsed as duration text; ints below 600 are assumed to
    be minutes (the legacy TMDB shape stored runtimes of 1..500), larger
    ints are already seconds. The edge: a sub-10-minute value already in
    seconds would be misread as minutes; real legacy data does not
    contain such values.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return parse_duration_text(value)
    if isinstance(value, (int, float)):
        v = int(value)
        if v <= 0:
            return None
        return v * 60 if v < 600 else v
    return None


def coerce_album_duration(value: str | int | float | None) -> int | None:
    """Coerce a legacy Album duration value into seconds.

    Values >= 50000 are assumed to be milliseconds (the legacy Album
    unit; even a 2-minute single is 120000 ms while a 13-hour box set is
    only 46800 s). Smaller values are already seconds.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip().isdigit():
            return None
        value = int(value.strip())
    if isinstance(value, (int, float)):
        v = int(value)
        if v <= 0:
            return None
        return v // 1000 if v >= 50000 else v
    return None


def duration_to_seconds(value: str | int | float | None) -> int | None:
    """Read-time tolerant conversion for stored duration values.

    Free-text values (pre-migration rows) are parsed; numeric values are
    trusted as seconds. Unlike the coerce_* helpers this never applies
    unit inference, so it is safe on already-converted data.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return parse_duration_text(value)
    if isinstance(value, (int, float)):
        return int(value) or None
    return None


def format_duration(value: int | None) -> str:
    """Format seconds for display: "2h 14m", "45m" or "58s"."""
    if not value:
        return ""
    seconds = int(value)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    if m:
        return f"{m}m"
    return f"{s}s"
