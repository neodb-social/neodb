"""
Country support utilities

Canonical representation for item origin countries is the ISO 3166-1
alpha-2 code ("US", "CN"). Normalization maps scraped names (Douban
Chinese names, English names, alpha-3 codes) to codes; unknown values
pass through as custom strings so historic regions (西德, 苏联) are
preserved rather than mis-mapped. Display names come from pycountry's
bundled iso3166-1 gettext catalogs, so no local translation entries are
needed.

Pattern follows common/models/genre.py.
"""

import gettext as gettext_module
from functools import lru_cache

import pycountry
from django.utils import translation


def _english_name(country) -> str:
    return getattr(country, "common_name", None) or country.name


COUNTRY_CHOICES: list[tuple[str, str]] = sorted(
    ((c.alpha_2, _english_name(c)) for c in pycountry.countries),
    key=lambda x: x[1],
)
COUNTRY_CODES: dict[str, str] = dict(COUNTRY_CHOICES)

# Explicit mappings from scraped strings to alpha-2 codes, mostly the
# names Douban uses for 制片国家/地区. Keys must be lowercase.
_SCRAPER_ALIASES: dict[str, str] = {
    "美国": "US",
    "中国大陆": "CN",
    "中国": "CN",
    "中国香港": "HK",
    "香港": "HK",
    "中国台湾": "TW",
    "台湾": "TW",
    "中国澳门": "MO",
    "澳门": "MO",
    "英国": "GB",
    "日本": "JP",
    "韩国": "KR",
    "朝鲜": "KP",
    "法国": "FR",
    "德国": "DE",
    "意大利": "IT",
    "西班牙": "ES",
    "葡萄牙": "PT",
    "加拿大": "CA",
    "澳大利亚": "AU",
    "新西兰": "NZ",
    "印度": "IN",
    "俄罗斯": "RU",
    "泰国": "TH",
    "荷兰": "NL",
    "比利时": "BE",
    "瑞士": "CH",
    "奥地利": "AT",
    "瑞典": "SE",
    "丹麦": "DK",
    "挪威": "NO",
    "芬兰": "FI",
    "冰岛": "IS",
    "波兰": "PL",
    "捷克": "CZ",
    "匈牙利": "HU",
    "希腊": "GR",
    "土耳其": "TR",
    "伊朗": "IR",
    "以色列": "IL",
    "埃及": "EG",
    "南非": "ZA",
    "巴西": "BR",
    "墨西哥": "MX",
    "阿根廷": "AR",
    "智利": "CL",
    "哥伦比亚": "CO",
    "新加坡": "SG",
    "马来西亚": "MY",
    "菲律宾": "PH",
    "越南": "VN",
    "印度尼西亚": "ID",
    "印尼": "ID",
    "爱尔兰": "IE",
    "乌克兰": "UA",
    "罗马尼亚": "RO",
    "保加利亚": "BG",
    "克罗地亚": "HR",
    "塞尔维亚": "RS",
    "沙特阿拉伯": "SA",
    "阿联酋": "AE",
    "卡塔尔": "QA",
    "黎巴嫩": "LB",
    "蒙古": "MN",
    "哈萨克斯坦": "KZ",
    "尼日利亚": "NG",
    "肯尼亚": "KE",
    "摩洛哥": "MA",
    "古巴": "CU",
    "秘鲁": "PE",
    "委内瑞拉": "VE",
    "乌拉圭": "UY",
    "爱沙尼亚": "EE",
    "拉脱维亚": "LV",
    "立陶宛": "LT",
    "斯洛伐克": "SK",
    "斯洛文尼亚": "SI",
    "卢森堡": "LU",
    "格鲁吉亚": "GE",
    # common English shorthands pycountry.lookup does not resolve
    "usa": "US",
    "uk": "GB",
    "south korea": "KR",
    "north korea": "KP",
    "russia": "RU",
    "vietnam": "VN",
}


@lru_cache(maxsize=1024)
def _normalize_country_str(v: str) -> str:
    if len(v) == 2 and v.upper() in COUNTRY_CODES:
        return v.upper()
    alias = _SCRAPER_ALIASES.get(v.lower())
    if alias:
        return alias
    try:
        return pycountry.countries.lookup(v).alpha_2
    except LookupError:
        return v


def normalize_country(value: str) -> str | None:
    """Normalize a single country value to an alpha-2 code.

    Returns the code when the value is a known code, alias or name;
    otherwise returns the input as-is (stripped) as a custom value.
    Cached: pycountry name lookups (and their LookupError misses for
    custom values) are relatively expensive in bulk operations.
    """
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    return _normalize_country_str(v)


def normalize_countries(values: list[str] | str | None) -> list[str]:
    """Normalize a list of country values, deduplicated, order kept."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    result = [c for c in (normalize_country(v) for v in values) if c]
    return list(dict.fromkeys(result))


# Django locale -> pycountry gettext locale; anything else tries the
# bare language subtag ("de", "fr", ...) which matches most catalogs.
_PYCOUNTRY_LOCALE_MAP = {
    "zh-hans": "zh_CN",
    "zh-hant": "zh_TW",
    "pt-br": "pt_BR",
}


@lru_cache(maxsize=None)
def _iso3166_translation(locale: str) -> gettext_module.NullTranslations:
    try:
        return gettext_module.translation(
            "iso3166-1", pycountry.LOCALES_DIR, languages=[locale]
        )
    except FileNotFoundError:
        return gettext_module.NullTranslations()


@lru_cache(maxsize=None)
def _display_name(code: str, lang: str) -> str:
    country = pycountry.countries.get(alpha_2=code)
    if country is None:
        return code
    if lang == "en" or lang.startswith("en-"):
        return _english_name(country)
    locale = _PYCOUNTRY_LOCALE_MAP.get(lang, lang.split("-")[0])
    t = _iso3166_translation(locale)
    for candidate in (getattr(country, "common_name", None), country.name):
        if candidate:
            translated = t.gettext(candidate)
            if translated != candidate:
                return translated
    return _english_name(country)


def country_display_name(code: str) -> str:
    """Display name for an alpha-2 code in the active UI language.

    Prefers the translated common name ("韩国") over the official one
    ("大韩民国"). Non-code custom values are returned verbatim. Cached per
    (code, language) since the mapping is static within a process.
    """
    if not code or not isinstance(code, str):
        return code or ""
    if len(code) != 2:
        return code
    return _display_name(code.upper(), (translation.get_language() or "en").lower())
