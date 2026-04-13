import re

from catalog.common import *
from catalog.models import *
from common.models.lang import detect_language

from .douban import DoubanDownloader


@SiteManager.register
class DoubanPersonage(AbstractSite):
    SITE_NAME = SiteName.Douban
    ID_TYPE = IdType.DoubanPersonage
    URL_PATTERNS = [
        r"\w+://www\.douban\.com/personage/(\d+)",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = People

    @classmethod
    def id_to_url(cls, id_value):
        return f"https://www.douban.com/personage/{id_value}/"

    def scrape(self):
        assert self.url
        content = DoubanDownloader(self.url).download().html()

        # Name from h1: "成龙 Jackie Chan" or "张艺谋" format
        raw_name = self.query_str(content, '//h1[@class="subject-name"]/text()')
        if not raw_name:
            raw_name = self.query_str(content, "//h1/text()")
        if not raw_name:
            raise ParseError(self, "name")
        raw_name = raw_name.strip()

        # Split Chinese and foreign name parts
        localized_name = []
        cn_name, foreign_name = _split_name(raw_name)
        if cn_name:
            localized_name.append({"lang": "zh-cn", "text": cn_name})
        if foreign_name:
            lang = detect_language(foreign_name)
            localized_name.append({"lang": lang, "text": foreign_name})

        if not localized_name:
            raise ParseError(self, "localized_name")

        # Extract label-value pairs from info section
        labels = self.query_list(content, '//span[@class="label"]/text()')
        values = self.query_list(content, '//span[@class="value"]/text()')
        info = {}
        for label, value in zip(labels, values):
            info[label.strip().rstrip(":")] = value.strip()

        # Birth date: "1954年4月7日" -> "1954-04-07"
        birth_date = _parse_douban_date(info.get("出生日期", ""))

        # Additional names
        alt_cn_names = info.get("更多中文名", "")
        alt_foreign_names = info.get("更多外文名", "")
        for name_str in _split_alt_names(alt_cn_names):
            if name_str and name_str not in [n["text"] for n in localized_name]:
                localized_name.append({"lang": "zh-cn", "text": name_str})
        for name_str in _split_alt_names(alt_foreign_names):
            if name_str and name_str not in [n["text"] for n in localized_name]:
                lang = detect_language(name_str)
                localized_name.append({"lang": lang, "text": name_str})

        # IMDb ID
        imdb_id = info.get("IMDb编号", "").strip()
        if imdb_id and not imdb_id.startswith("nm"):
            imdb_id = ""

        # Photo: upgrade from /m/ (medium) to /raw/ (full size)
        photo_url = self.query_str(content, '//img[@class="avatar"]/@src')
        if photo_url:
            photo_url = photo_url.strip()
            photo_url = photo_url.replace("/m/", "/raw/")

        # Bio
        bio_elem = self.query_list(
            content, '//div[@class="desc"]/div[@class="content"]/text()'
        )
        bio = "\n".join([t.strip() for t in bio_elem if t.strip()])

        localized_bio = [{"lang": "zh-cn", "text": bio}] if bio else []

        pd = ResourceContent(
            metadata={
                "localized_name": localized_name,
                "localized_bio": localized_bio,
                "title": cn_name or foreign_name or raw_name,
                "birth_date": birth_date,
                "cover_image_url": photo_url,
            }
        )

        if imdb_id:
            pd.lookup_ids[IdType.IMDB] = imdb_id

        return pd


def _split_name(raw_name: str) -> tuple[str | None, str | None]:
    """Split a combined Chinese/foreign name like '成龙 Jackie Chan'.

    Returns (cn_name, foreign_name). Either may be None.
    """
    has_cjk = bool(re.search(r"[\u4e00-\u9fff\u3400-\u4dbf]", raw_name))
    has_latin = bool(re.search(r"[a-zA-Z]", raw_name))

    if has_cjk and has_latin:
        # CJK part then Latin part, separated by space
        m = re.match(
            r"([\u4e00-\u9fff\u3400-\u4dbf\u00b7\s]+)\s+([a-zA-Z].*)", raw_name
        )
        if m:
            return m.group(1).strip(), m.group(2).strip()
        # Latin then CJK
        m = re.match(
            r"([a-zA-Z][^\u4e00-\u9fff\u3400-\u4dbf]*)\s+([\u4e00-\u9fff].*)",
            raw_name,
        )
        if m:
            return m.group(2).strip(), m.group(1).strip()
    if has_cjk:
        return raw_name, None
    if has_latin:
        return None, raw_name
    return raw_name, None


def _parse_douban_date(date_str: str) -> str | None:
    """Parse Douban date format '1954年4月7日' to ISO '1954-04-07'."""
    if not date_str:
        return None
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"(\d{4})年(\d{1,2})月", date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4})年", date_str)
    if m:
        return m.group(1)
    return date_str.strip() or None


def _split_alt_names(names_str: str) -> list[str]:
    """Split '房仕龙(本名) / 陈港生(原名)' into ['房仕龙', '陈港生']."""
    if not names_str:
        return []
    parts = names_str.split(" / ")
    result = []
    for part in parts:
        name = re.sub(r"\s*[\(（][^)）]*[\)）]\s*", "", part).strip()
        if name:
            result.append(name)
    return result
