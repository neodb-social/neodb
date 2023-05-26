import requests
import filetype
from PIL import Image
from io import BytesIO
from requests.exceptions import RequestException
from django.conf import settings
from pathlib import Path
import json
from io import StringIO
import re
import time
import logging
from lxml import html
from urllib.parse import quote

_logger = logging.getLogger(__name__)


RESPONSE_OK = 0  # response is ready for pasring
RESPONSE_INVALID_CONTENT = -1  # content not valid but no need to retry
RESPONSE_NETWORK_ERROR = -2  # network error, retry next proxied url
RESPONSE_CENSORSHIP = -3  # censored, try sth special if possible

_mock_mode = False


def use_local_response(func):
    def _func(args):
        set_mock_mode(True)
        func(args)
        set_mock_mode(False)

    return _func


def set_mock_mode(enabled):
    global _mock_mode
    _mock_mode = enabled


def get_mock_mode():
    global _mock_mode
    return _mock_mode


def get_mock_file(url):
    fn = url.replace("***REMOVED***", "1234")  # Thank you, Github Action -_-!
    fn = re.sub(r"[^\w]", "_", fn)
    fn = re.sub(r"_key_[*A-Za-z0-9]+", "_key_8964", fn)
    if len(fn) > 255:
        fn = fn[:255]
    return fn


class DownloadError(Exception):
    def __init__(self, downloader, msg=None):
        self.url = downloader.url
        self.logs = downloader.logs
        if downloader.response_type == RESPONSE_INVALID_CONTENT:
            error = "Invalid Response"
        elif downloader.response_type == RESPONSE_NETWORK_ERROR:
            error = "Network Error"
        elif downloader.response_type == RESPONSE_NETWORK_ERROR:
            error = "Censored Content"
        else:
            error = "Unknown Error"
        self.message = (
            f"Download Failed: {error}{', ' + msg if msg else ''}, url: {self.url}"
        )
        super().__init__(self.message)


class BasicDownloader:
    headers = {
        # "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:107.0) Gecko/20100101 Firefox/107.0",
        "User-Agent": "Mozilla/5.0 (iPad; CPU OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
    }

    def __init__(self, url, headers=None):
        self.url = url
        self.response_type = RESPONSE_OK
        self.logs = []
        if headers:
            self.headers = headers

    def get_timeout(self):
        return settings.SCRAPING_TIMEOUT

    def validate_response(self, response):
        if response is None:
            return RESPONSE_NETWORK_ERROR
        elif response.status_code == 200:
            return RESPONSE_OK
        else:
            return RESPONSE_INVALID_CONTENT

    def _download(self, url):
        try:
            if not _mock_mode:
                # TODO cache = get/set from redis
                resp = requests.get(
                    url, headers=self.headers, timeout=self.get_timeout()
                )
                if settings.DOWNLOADER_SAVEDIR:
                    try:
                        with open(
                            settings.DOWNLOADER_SAVEDIR + "/" + get_mock_file(url),
                            "w",
                            encoding="utf-8",
                        ) as fp:
                            fp.write(resp.text)
                    except:
                        _logger.warn("Save downloaded data failed.")
            else:
                resp = MockResponse(self.url)
            response_type = self.validate_response(resp)
            self.logs.append(
                {"response_type": response_type, "url": url, "exception": None}
            )

            return resp, response_type
        except RequestException as e:
            self.logs.append(
                {"response_type": RESPONSE_NETWORK_ERROR, "url": url, "exception": e}
            )
            return None, RESPONSE_NETWORK_ERROR

    def download(self):
        resp, self.response_type = self._download(self.url)
        if self.response_type == RESPONSE_OK and resp:
            return resp
        else:
            raise DownloadError(self)


class ProxiedDownloader(BasicDownloader):
    def get_proxied_urls(self):
        urls = []
        if settings.SCRAPESTACK_KEY is not None:
            # urls.append(f'http://api.scrapestack.com/scrape?access_key={settings.SCRAPESTACK_KEY}&url={self.url}')
            urls.append(
                f"http://api.scrapestack.com/scrape?keep_headers=1&access_key={settings.SCRAPESTACK_KEY}&url={quote(self.url)}"
            )
        if settings.PROXYCRAWL_KEY is not None:
            urls.append(
                f"https://api.proxycrawl.com/?token={settings.PROXYCRAWL_KEY}&url={quote(self.url)}"
            )
        if settings.SCRAPERAPI_KEY is not None:
            urls.append(
                f"http://api.scraperapi.com/?api_key={settings.SCRAPERAPI_KEY}&url={quote(self.url)}"
            )
        return urls

    def get_special_proxied_url(self):
        return (
            f"{settings.LOCAL_PROXY}?url={quote(self.url)}"
            if settings.LOCAL_PROXY is not None
            else None
        )

    def download(self):
        urls = self.get_proxied_urls()
        last_try = False
        url = urls.pop(0) if len(urls) else None
        resp = None
        resp_type = None
        while url:
            resp, resp_type = self._download(url)
            if (
                resp_type == RESPONSE_OK
                or resp_type == RESPONSE_INVALID_CONTENT
                or last_try
            ):
                url = None
            elif resp_type == RESPONSE_CENSORSHIP:
                url = self.get_special_proxied_url()
                last_try = True
            else:  # resp_type == RESPONSE_NETWORK_ERROR:
                url = urls.pop(0) if len(urls) else None
        self.response_type = resp_type
        if self.response_type == RESPONSE_OK:
            return resp
        else:
            raise DownloadError(self)


class RetryDownloader(BasicDownloader):
    def download(self):
        retries = settings.DOWNLOADER_RETRIES
        while retries:
            retries -= 1
            resp, self.response_type = self._download(self.url)
            if self.response_type == RESPONSE_OK:
                return resp
            elif self.response_type != RESPONSE_NETWORK_ERROR and retries == 0:
                raise DownloadError(self)
            elif retries > 0:
                _logger.debug("Retry " + self.url)
                time.sleep((settings.DOWNLOADER_RETRIES - retries) * 0.5)
        raise DownloadError(self, "max out of retries")


class ImageDownloaderMixin:
    def __init__(self, url, referer=None):
        if referer is not None:
            self.headers["Referer"] = referer
        super().__init__(url)

    def validate_response(self, response):
        if response and response.status_code == 200:
            try:
                raw_img = response.content
                img = Image.open(BytesIO(raw_img))
                img.load()  # corrupted image will trigger exception
                content_type = response.headers.get("Content-Type")
                file_type = filetype.get_type(
                    mime=content_type.partition(";")[0].strip()
                )
                if file_type is None:
                    return RESPONSE_NETWORK_ERROR
                self.extention = file_type.extension
                return RESPONSE_OK
            except Exception:
                return RESPONSE_NETWORK_ERROR
        if response and response.status_code >= 400 and response.status_code < 500:
            return RESPONSE_INVALID_CONTENT
        else:
            return RESPONSE_NETWORK_ERROR

    @classmethod
    def download_image(cls, image_url, page_url, headers=None):
        imgdl = cls(image_url, page_url)
        if headers is not None:
            imgdl.headers = headers
        try:
            image = imgdl.download().content
            image_extention = imgdl.extention
            return image, image_extention
        except Exception:
            return None, None


class BasicImageDownloader(ImageDownloaderMixin, BasicDownloader):
    pass


class ProxiedImageDownloader(ImageDownloaderMixin, ProxiedDownloader):
    pass


_local_response_path = (
    str(Path(__file__).parent.parent.parent.absolute()) + "/test_data/"
)


class MockResponse:
    def __init__(self, url):
        self.url = url
        fn = _local_response_path + get_mock_file(url)
        try:
            self.content = Path(fn).read_bytes()
            self.status_code = 200
            _logger.debug(f"use local response for {url} from {fn}")
        except Exception:
            self.content = b"Error: response file not found"
            self.status_code = 404
            _logger.debug(f"local response not found for {url} at {fn}")

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return json.load(StringIO(self.text))

    def html(self):
        return html.fromstring(
            self.content.decode("utf-8")
        )  # may throw exception unexpectedly due to OS bug, see https://github.com/neodb-social/neodb/issues/5

    @property
    def headers(self):
        return {
            "Content-Type": "image/jpeg" if self.url.endswith("jpg") else "text/html"
        }


requests.Response.html = MockResponse.html
