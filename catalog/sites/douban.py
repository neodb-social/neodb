import re

from catalog.common import *

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
            if content.find("关于豆瓣") == -1:
                # if content.find('你的 IP 发出') == -1:
                #     error = error + 'Content not authentic'  # response is garbage
                # else:
                #     error = error + 'IP banned'
                return RESPONSE_NETWORK_ERROR
            elif (
                content.find("<title>页面不存在</title>") != -1
                or content.find("呃... 你想访问的条目豆瓣不收录。") != -1
            ):  # re.search('不存在[^<]+</title>', content, re.MULTILINE):
                return RESPONSE_CENSORSHIP
            else:
                return RESPONSE_OK
        else:
            return RESPONSE_INVALID_CONTENT
