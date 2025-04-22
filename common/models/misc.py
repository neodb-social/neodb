import re
from datetime import datetime


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
