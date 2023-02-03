import re


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
