"""
Price normalization

Canonical price form is "<ISO 4217 code> <amount>", e.g. "CNY 26" or
"USD 10.99". Unambiguous Chinese currency names ("99 美元", "66日元")
normalize as well. Values that cannot be normalized unambiguously
(ranges, multiple prices, bare numbers or ¥/元 without a source hint)
are kept as-is rather than guessed.
"""

import re

_RE_CANONICAL = re.compile(r"^[A-Z]{3} \d+(\.\d+)?$")
_RE_PREFIXED = re.compile(
    r"^([A-Za-z]{2,4}|US\$|NT\$|HK\$|[$€£¥￥])\s*([\d,]+(?:\.\d+)?)$"
)
# suffix accepts short CJK currency words (元, 円, 美元, 人民币, ...);
# unrecognized words resolve to no currency and the value is kept
_RE_SUFFIXED = re.compile(
    r"^([\d,]+(?:\.\d+)?)\s*([A-Za-z]{2,4}|[\u4e00-\u9fff]{1,4})$"
)
_RE_BARE = re.compile(r"^([\d,]+(?:\.\d+)?)$")

# non-ISO codes and symbols that map to one currency unambiguously
_CURRENCY_ALIASES = {
    "NTD": "TWD",
    "NT$": "TWD",
    "RMB": "CNY",
    "US$": "USD",
    "USD$": "USD",
    "HK$": "HKD",
    "€": "EUR",
    "EURO": "EUR",
    "£": "GBP",
    "円": "JPY",
    "YEN": "JPY",
    "WON": "KRW",
    # Chinese currency names (suffix form, e.g. "99 美元")
    "美元": "USD",
    "美金": "USD",
    "日元": "JPY",
    "日圆": "JPY",
    "港元": "HKD",
    "港币": "HKD",
    "人民币": "CNY",
    "欧元": "EUR",
    "英镑": "GBP",
    "新台币": "TWD",
    "台币": "TWD",
    "韩元": "KRW",
}
# symbols/words that are ambiguous without knowing the source site
_HINT_ONLY = {"¥", "￥", "元"}


def _resolve_currency(token: str, default_currency: str | None) -> str | None:
    t = token.strip().upper()
    if t in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[t]
    if t in _HINT_ONLY or token in _HINT_ONLY:
        return default_currency
    if len(t) == 3 and t.isalpha():
        return t
    return None


def normalize_price(value: str, default_currency: str | None = None) -> str:
    """Normalize a scraped price into "<ISO4217> <amount>" when possible.

    ``default_currency`` is a source hint (e.g. "CNY" for douban_book,
    "JPY" for bangumi) used for bare numbers and ambiguous ¥/元 marks.
    Idempotent; returns the input unchanged when parsing would require
    guessing.
    """
    if not value or not isinstance(value, str):
        return value
    s = " ".join(value.split())
    if _RE_CANONICAL.match(s):
        return s
    m = _RE_PREFIXED.match(s)
    if m:
        currency = _resolve_currency(m.group(1), default_currency)
        if currency:
            return f"{currency} {m.group(2).replace(',', '')}"
        return value
    m = _RE_SUFFIXED.match(s)
    if m:
        currency = _resolve_currency(m.group(2), default_currency)
        if currency:
            return f"{currency} {m.group(1).replace(',', '')}"
        return value
    m = _RE_BARE.match(s)
    if m and default_currency:
        return f"{default_currency} {m.group(1).replace(',', '')}"
    return value
