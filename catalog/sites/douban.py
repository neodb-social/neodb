import json
import re

from catalog.common import *
from catalog.search.models import ExternalSearchResultItem

RE_NUMBERS = re.compile(r"\d+\d*")
RE_WHITESPACES = re.compile(r"\s+")


class DoubanDownloader(ProxiedDownloader):
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
            content.xpath("//script[text()[contains(.,'window.__DATA__')]]/text()")[  # type:ignore
                0
            ]
            .split("window.__DATA__ = ")[1]  # type:ignore
            .split("};")[0]  # type:ignore
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
            for item in j["items"]
            if item.get("tpl_name") == "search_subject"
        ]
        return results
