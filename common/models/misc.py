def uniq(ls: list) -> list:
    r = []
    for i in ls:
        if i not in r:
            r.append(i)
    return r
