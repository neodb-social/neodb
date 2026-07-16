"""
Partial ISO date support

A "partial date" is a string of "YYYY", "YYYY-MM" or "YYYY-MM-DD" (zero
padded). It is the canonical representation for catalog release dates,
where sources may only know the year or the month of a release.
"""

import re
from collections.abc import Iterable
from datetime import date, datetime

_RE_PARTIAL_DATE = re.compile(
    r"^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2})(?:[T ].*)?)?)?$"
)


def parse_partial_date(
    value: str | int | date | datetime | None,
) -> str | None:
    """Parse a value into canonical partial date form, or None.

    Accepts date/datetime objects, int years, and strings shaped like
    "YYYY", "YYYY-MM", "YYYY-MM-DD" (also with "/" or "." separators, or
    a trailing time part). Out-of-range month/day parts are truncated to
    the valid prefix rather than rejected.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return str(value) if 1000 <= value <= 2999 else None
    if not isinstance(value, str):
        return None
    m = _RE_PARTIAL_DATE.match(value.strip())
    if not m:
        return None
    year, month, day = m.group(1), m.group(2), m.group(3)
    if not month or not 1 <= int(month) <= 12:
        return year
    if not day:
        return f"{year}-{int(month):02d}"
    try:
        date(int(year), int(month), int(day))
    except ValueError:
        return f"{year}-{int(month):02d}"
    return f"{year}-{int(month):02d}-{int(day):02d}"


def year_of_partial_date(value: str | None) -> int | None:
    """Return the year of a partial date string, or None."""
    d = parse_partial_date(value)
    return int(d[:4]) if d else None


def partial_date_to_int(value: str | None) -> int:
    """Return YYYYMMDD as int, with missing month/day as 00.

    Matches the catalog search index "date" facet convention, so a bare
    year "2010" (20100000) still falls in the year:2010 filter range
    20100000..20109999. Returns 0 for unparseable input.
    """
    d = parse_partial_date(value)
    if not d:
        return 0
    parts = d.split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 0
    day = int(parts[2]) if len(parts) > 2 else 0
    return year * 10000 + month * 100 + day


def _sort_key(d: str) -> tuple[int, int, int]:
    # missing parts sort high so a specific date beats a bare year of the
    # same year, while an earlier year always wins
    parts = d.split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 13
    day = int(parts[2]) if len(parts) > 2 else 32
    return year, month, day


def earliest_partial_date(
    values: Iterable[str | int | date | datetime | None],
) -> str | None:
    """Return the earliest of the given dates in canonical partial form."""
    parsed = [d for d in (parse_partial_date(v) for v in values) if d]
    return min(parsed, key=_sort_key) if parsed else None
