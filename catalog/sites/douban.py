import json
import re

from ..common import *
from ..models import IdType, ItemCategory, SiteName
from ..search import ExternalSearchResultItem

RE_NUMBERS = re.compile(r"\d+\d*")
RE_WHITESPACES = re.compile(r"\s+")

_RE_PERSONAGE = re.compile(r"personage/(\d+)")
_RE_AUTHOR = re.compile(r"(?:https?://book\.douban\.com)?/author/(\d+)")
_RE_MUSICIAN = re.compile(r"(?:https?://music\.douban\.com)?/musician/(\d+)")

_AUTHOR_URL_FMT = "https://book.douban.com/author/{}/"
_MUSICIAN_URL_FMT = "https://music.douban.com/musician/{}/"


def extract_people_links_from_anchors(anchors: list, limit: int = 15) -> list[dict]:
    """Extract Douban person links from <a> elements.

    Handles personage/N (direct), author/N, and musician/N (redirect) patterns.
    Both absolute and relative URLs are supported.
    """
    seen: set[str] = set()
    resources: list[dict] = []
    for a in anchors:
        href = a.get("href", "")
        if not href:
            continue
        # Direct personage link
        m = _RE_PERSONAGE.search(href)
        if m:
            pid = m.group(1)
            if pid in seen:
                continue
            seen.add(pid)
            resources.append(
                {
                    "model": "People",
                    "id_type": IdType.DoubanPersonage,
                    "id_value": pid,
                    "url": f"https://www.douban.com/personage/{pid}/",
                }
            )
        else:
            # Author or musician link -- resolved via HTTP redirect
            m_author = _RE_AUTHOR.search(href)
            m_musician = _RE_MUSICIAN.search(href)
            if m_author:
                url = _AUTHOR_URL_FMT.format(m_author.group(1))
            elif m_musician:
                url = _MUSICIAN_URL_FMT.format(m_musician.group(1))
            else:
                continue
            if url in seen:
                continue
            seen.add(url)
            resources.append({"url": url})
        if len(resources) >= limit:
            break
    return resources


class DoubanDownloader(ScrapDownloader):
    def __init__(
        self,
        url: str,
        headers: dict | None = None,
        timeout: float | None = None,
    ):
        super().__init__(url, headers, timeout, "#content")

    def validate_response(self, response) -> int:
        if response is None:
            return RESPONSE_NETWORK_ERROR
        elif response.status_code == 204:
            return RESPONSE_CENSORSHIP
        elif response.status_code == 200:
            content = response.content.decode("utf-8")
            if content.find("关于豆瓣") == -1 and content.find("豆瓣评分") == -1:
                # if content.find('你的 IP 发出') == -1:
                #     error = error + 'Content not authentic'  # response is garbage
                # else:
                #     error = error + 'IP banned'
                return RESPONSE_NETWORK_ERROR
            elif (
                content.find("<title>页面不存在</title>") != -1
                or content.find("呃... 你想访问的条目豆瓣不收录。") != -1
                or content.find("根据相关法律法规，当前条目正在等待审核。") != -1
            ):  # re.search('不存在[^<]+</title>', content, re.MULTILINE):
                return RESPONSE_CENSORSHIP
            else:
                return RESPONSE_OK
        else:
            return RESPONSE_INVALID_CONTENT


class DoubanSearcher:
    @classmethod
    def search(cls, cat: ItemCategory, c: str, q: str, p: int = 1):
        url = f"https://search.douban.com/{c}/subject_search?search_text={q}&start={15 * (p - 1)}"
        content = DoubanDownloader(url).download().html()
        j = json.loads(
            content.xpath("//script[text()[contains(.,'window.__DATA__')]]/text()")[0]
            .split("window.__DATA__ = ")[1]
            .split("};")[0]
            + "}"
        )
        results = [
            ExternalSearchResultItem(
                cat,
                SiteName.Douban,
                item["url"],
                item["title"],
                item["abstract"],
                item["abstract_2"],
                item["cover_url"],
            )
            for item in j["items"]
            if item.get("tpl_name") == "search_subject"
        ]
        return results
