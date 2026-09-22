import os
import re
from datetime import datetime

from django.db.models.fields.files import FieldFile

# Stored marker for missing artwork, independent of the displayed fallback.
MISSING_COVER = "item/default.svg"


def is_missing_cover(cover: FieldFile | str | None) -> bool:
    """True when ``cover`` holds no real artwork.

    Older rows carry markers other than MISSING_COVER (``collection/default.svg``,
    a bare ``default.svg``). None of them was ever a stored file, so every
    ``default.svg`` path counts as missing; an uploaded cover cannot collide,
    because every upload_to helper names it ``<uuid>.<ext>``.

    Accepts a ``FieldFile`` or a plain name, since call sites hold either.
    """
    name = str(cover or "")
    return not name or os.path.basename(name) == "default.svg"


def uniq(ls: list) -> list:
    r = []
    for i in ls:
        if i not in r:
            r.append(i)
    return r


def int_(x, default=0):
    return (
        x
        if isinstance(x, int)
        else (int(x) if (isinstance(x, str) and x.isdigit()) else default)
    )


def datetime_(dt) -> datetime | None:
    if not dt:
        return None
    try:
        if re.match(r"\d{4}-\d{1,2}-\d{1,2}", dt):
            d = datetime.strptime(dt, "%Y-%m-%d")
        elif re.match(r"\d{4}-\d{1,2}", dt):
            d = datetime.strptime(dt, "%Y-%m")
        elif re.match(r"\d{4}", dt):
            d = datetime.strptime(dt, "%Y")
        else:
            return None
        return d
    except ValueError:
        return None
