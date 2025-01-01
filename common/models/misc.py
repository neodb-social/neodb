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
