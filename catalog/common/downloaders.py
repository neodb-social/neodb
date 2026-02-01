import json
import re
import time
from io import BytesIO, StringIO
from pathlib import Path
from typing import Tuple, Union, cast
from urllib.parse import quote, urlencode

import filetype
import httpx
import requests
from django.conf import settings
from django.core.cache import cache
from loguru import logger
from lxml import etree, html
from PIL import Image
from requests import Response
from requests.exceptions import RequestException

RESPONSE_OK = 0  # response is ready for pasring
RESPONSE_INVALID_CONTENT = -1  # content not valid but no need to retry
RESPONSE_NETWORK_ERROR = -2  # network error, retry next proxied url
RESPONSE_CENSORSHIP = -3  # censored, try sth special if possible
RESPONSE_QUOTA_EXCEEDED = -4

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
    fn = re.sub(r"key=[*A-Za-z0-9_\-]+", "key_8964", fn)
    fn = re.sub(r"[^\w]", "_", fn)
    if len(fn) > 255:
        fn = fn[:255]
    return fn


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
            # logger.debug(f"use local response for {url} from {fn}")
        except Exception:
            self.content = b"Error: response file not found"
            self.status_code = 404
            if ".jpg" not in self.url:
                logger.warning(f"local response not found for {url} at {fn}")

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return json.load(StringIO(self.text))

    def html(self):
        return html.fromstring(  # may throw exception unexpectedly due to OS bug, see https://github.com/neodb-social/neodb/issues/5
            self.content.decode("utf-8")
        )

    def xml(self):
        return etree.fromstring(self.content, base_url=self.url)

    @property
    def headers(self):
        return {"content-type": "image/jpeg" if ".jpg" in self.url else "text/html"}


class DownloaderResponse(Response):
    url: str

    def html(self):
        return html.fromstring(  # may throw exception unexpectedly due to OS bug, see https://github.com/neodb-social/neodb/issues/5
            self.content.decode("utf-8")
        )

    def xml(self):
        return etree.fromstring(self.content, base_url=self.url)


class ScraperResponse:
    """Response class for scraper API results, similar to MockResponse."""

    def __init__(
        self,
        url: str,
        content: bytes,
        status_code: int = 200,
        headers: dict | None = None,
    ):
        self.url = url
        self.content = content
        self.status_code = status_code
        self._headers = headers or {"content-type": "text/html"}

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return json.load(StringIO(self.text))

    def html(self):
        return html.fromstring(self.content.decode("utf-8"))

    def xml(self):
        return etree.fromstring(self.content, base_url=self.url)

    @property
    def headers(self):
        return self._headers


class DownloaderResponse2(httpx.Response):
    def html(self):
        return html.fromstring(self.content.decode("utf-8"))

    def xml(self):
        return etree.fromstring(self.content, base_url=str(self.url))


# Type alias for all response types returned by downloaders
ResponseType = Union[
    DownloaderResponse, DownloaderResponse2, MockResponse, ScraperResponse
]


class DownloadError(Exception):
    def __init__(self, downloader, msg=None):
        self.url = downloader.url
        self.logs = downloader.logs
        self.response_type = downloader.response_type
        if downloader.response_type == RESPONSE_INVALID_CONTENT:
            error = "Invalid Response"
        elif downloader.response_type == RESPONSE_NETWORK_ERROR:
            error = "Network Error"
        elif downloader.response_type == RESPONSE_CENSORSHIP:
            error = "Censored Content"
        elif downloader.response_type == RESPONSE_QUOTA_EXCEEDED:
            error = "API Quota Exceeded"
        else:
            error = "Unknown Error"
        self.message = (
            f"Download Failed: {error}{', ' + msg if msg else ''}, url: {self.url}"
        )
        super().__init__(self.message)


class BasicDownloader:
    @staticmethod
    def get_accept_language():
        match settings.LANGUAGE_CODE:
            case "zh-hans":
                return "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2"
            case "zh-hant":
                return "zh-TW,zh-HK;q=0.7,zh;q=0.5,en-US;q=0.3,en;q=0.2"
            case _:
                return "en-US;q=0.3,en;q=0.2"

    headers = {
        # "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:107.0) Gecko/20100101 Firefox/107.0",
        "User-Agent": "Mozilla/5.0 (iPad; CPU OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": get_accept_language(),
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
    }

    timeout = settings.DOWNLOADER_REQUEST_TIMEOUT

    def __init__(self, url, headers: dict | None = None, timeout: float | None = None):
        self.url = url
        self.response_type = RESPONSE_OK
        self.logs = []
        if headers:
            self.headers = headers
        if timeout:
            self.timeout = timeout

    def validate_response(self, response) -> int:
        if response is None:
            return RESPONSE_NETWORK_ERROR
        elif response.status_code == 200:
            return RESPONSE_OK
        elif response.status_code == 429:
            return RESPONSE_QUOTA_EXCEEDED
        else:
            return RESPONSE_INVALID_CONTENT

    def _download(
        self, url
    ) -> Tuple[DownloaderResponse | DownloaderResponse2 | MockResponse | None, int]:
        try:
            if not _mock_mode:
                resp = cast(
                    DownloaderResponse,
                    requests.get(url, headers=self.headers, timeout=self.timeout),
                )
                resp.__class__ = DownloaderResponse
                if settings.DOWNLOADER_SAVEDIR:
                    try:
                        with open(
                            settings.DOWNLOADER_SAVEDIR + "/" + get_mock_file(url),
                            "w",
                            encoding="utf-8",
                        ) as fp:
                            fp.write(resp.text)
                    except Exception:
                        logger.warning("Save downloaded data failed.")
            else:
                resp = MockResponse(self.url)
            response_type = self.validate_response(resp)
            self.logs.append(
                {"response_type": response_type, "url": url, "exception": None}
            )
            return resp, response_type
        except RequestException as e:
            # logger.debug(f"RequestException: {e}")
            self.logs.append(
                {"response_type": RESPONSE_NETWORK_ERROR, "url": url, "exception": e}
            )
            return None, RESPONSE_NETWORK_ERROR

    def download(self) -> ResponseType:
        resp, self.response_type = self._download(self.url)
        if self.response_type == RESPONSE_OK and resp:
            return resp
        raise DownloadError(self)


class BasicDownloader2(BasicDownloader):
    def _download(self, url):
        try:
            if not _mock_mode:
                resp = cast(
                    DownloaderResponse2,
                    httpx.get(url, headers=self.headers, timeout=self.timeout),
                )
                resp.__class__ = DownloaderResponse2
                if settings.DOWNLOADER_SAVEDIR:
                    try:
                        with open(
                            settings.DOWNLOADER_SAVEDIR + "/" + get_mock_file(url),
                            "w",
                            encoding="utf-8",
                        ) as fp:
                            fp.write(resp.text)
                    except Exception:
                        logger.warning("Save downloaded data failed.")
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

    def download(self) -> ResponseType:
        resp, self.response_type = self._download(self.url)
        if self.response_type == RESPONSE_OK and resp:
            return resp
        raise DownloadError(self)


class ProxiedDownloader(BasicDownloader):
    def get_proxied_urls(self):
        if not settings.DOWNLOADER_PROXY_LIST:
            return [self.url]
        urls = []
        for p in settings.DOWNLOADER_PROXY_LIST:
            urls.append(p.replace("__URL__", quote(self.url)))
        return urls

    def get_special_proxied_url(self):
        return (
            settings.DOWNLOADER_BACKUP_PROXY.replace("__URL__", quote(self.url))
            if settings.DOWNLOADER_BACKUP_PROXY
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
        if self.response_type == RESPONSE_OK and resp:
            return resp
        else:
            raise DownloadError(self)


class RetryDownloader(BasicDownloader):
    def download(self):
        retries = settings.DOWNLOADER_RETRIES
        while retries:
            retries -= 1
            resp, self.response_type = self._download(self.url)
            if self.response_type == RESPONSE_OK and resp:
                return resp
            elif self.response_type != RESPONSE_NETWORK_ERROR and retries == 0:
                raise DownloadError(self)
            elif retries > 0:
                logger.debug("Retry " + self.url)
                time.sleep((settings.DOWNLOADER_RETRIES - retries) * 0.5)
        raise DownloadError(self, "max out of retries")


class CachedDownloader(BasicDownloader):
    def download(self):
        cache_key = "dl:" + self.url
        resp = cache.get(cache_key)
        if resp:
            self.response_type = RESPONSE_OK
        else:
            resp = super().download()
            if self.response_type == RESPONSE_OK:
                cache.set(cache_key, resp, timeout=settings.DOWNLOADER_CACHE_TIMEOUT)
        return resp


class ImageDownloaderMixin:
    def __init__(self, url, referer=None):
        self.extention = None
        if referer is not None:
            self.headers["Referer"] = referer  # type: ignore
        super().__init__(url)  # type: ignore

    def validate_response(self, response):
        if response and response.status_code == 200:
            try:
                content_type = response.headers.get("content-type", "")
                if content_type.startswith("image/svg+xml"):
                    self.extention = "svg"
                    return RESPONSE_OK
                file_type = filetype.get_type(
                    mime=content_type.partition(";")[0].strip()
                )
                if file_type is None:
                    logger.error(
                        f"Unsupported image type: {content_type}",
                        extra={"url": response.url},
                    )
                    return RESPONSE_NETWORK_ERROR
                self.extention = file_type.extension
                raw_img = response.content
                img = Image.open(BytesIO(raw_img))
                img.load()  # corrupted image will trigger exception
                return RESPONSE_OK
            except Exception as e:
                logger.error(
                    f"Invalid downloaded image {e}", extra={"url": response.url}
                )
                return RESPONSE_NETWORK_ERROR
        if response and response.status_code >= 400 and response.status_code < 500:
            return RESPONSE_INVALID_CONTENT
        else:
            return RESPONSE_NETWORK_ERROR

    @classmethod
    def download_image(cls, image_url, page_url, headers=None):
        imgdl: BasicDownloader = cls(image_url, page_url)  # type:ignore
        if headers is not None:
            imgdl.headers = headers
        try:
            image = imgdl.download().content
            image_extention = imgdl.extention  # type:ignore
            return image, image_extention
        except Exception:
            return None, None


class BasicImageDownloader(ImageDownloaderMixin, BasicDownloader):
    pass


class ProxiedImageDownloader(ImageDownloaderMixin, ProxiedDownloader):
    pass


class ScrapDownloader(BasicDownloader):
    """
    Downloader that uses third-party scraping APIs with JavaScript rendering support.

    Supported providers:
      - scrapfly    : https://scrapfly.io
      - decodo      : https://decodo.com
      - scraperapi  : https://scraperapi.com
      - scrapingbee : https://scrapingbee.com
      - custom      : Custom URL template

    Configuration (via Django settings):
      DOWNLOADER_PROVIDERS         - Comma-separated list of providers to try in order
      DOWNLOADER_SCRAPFLY_KEY      - Scrapfly API key
      DOWNLOADER_DECODO_TOKEN      - Decodo Base64 Basic auth token
      DOWNLOADER_SCRAPERAPI_KEY    - ScraperAPI key
      DOWNLOADER_SCRAPINGBEE_KEY   - ScrapingBee API key
      DOWNLOADER_CUSTOMSCRAPER_URL - Custom URL with __URL__ and __SELECTOR__ placeholders
    """

    def __init__(
        self,
        url,
        headers: dict | None = None,
        timeout: float | None = None,
        wait_for_selector: str | None = None,
    ):
        super().__init__(url, headers, timeout)
        self.wait_for_selector = wait_for_selector

    def _scrape_with_scrapfly(self, api_key: str) -> Tuple[ResponseType | None, int]:
        """Scrape using Scrapfly API."""
        params = {
            "key": api_key,
            "url": self.url,
            "render_js": "true",
        }
        if self.wait_for_selector:
            params["wait_for_selector"] = self.wait_for_selector

        api_url = f"https://api.scrapfly.io/scrape?{urlencode(params)}"

        try:
            response = requests.get(api_url, timeout=self.timeout)

            if response.status_code == 200:
                data = response.json()
                content = data.get("result", {}).get("content", "")
                resp = ScraperResponse(self.url, content.encode("utf-8"))

                self.logs.append(
                    {
                        "response_type": RESPONSE_OK,
                        "url": api_url,
                        "provider": "scrapfly",
                        "exception": None,
                    }
                )
                return resp, RESPONSE_OK
            elif response.status_code == 429:
                reject_code = response.headers.get("X-Scrapfly-Reject-Code", "")
                reject_desc = response.headers.get("X-Scrapfly-Reject-Description", "")
                logger.warning(
                    f"Scrapfly quota exceeded: {reject_code} - {reject_desc}"
                )
                self.logs.append(
                    {
                        "response_type": RESPONSE_QUOTA_EXCEEDED,
                        "url": api_url,
                        "provider": "scrapfly",
                        "exception": f"{reject_code}: {reject_desc}",
                    }
                )
                return None, RESPONSE_QUOTA_EXCEEDED
            else:
                self.logs.append(
                    {
                        "response_type": RESPONSE_NETWORK_ERROR,
                        "url": api_url,
                        "provider": "scrapfly",
                        "exception": f"HTTP {response.status_code}",
                    }
                )
                return None, RESPONSE_NETWORK_ERROR

        except Exception as e:
            logger.debug(f"Scrapfly error: {e}")
            self.logs.append(
                {
                    "response_type": RESPONSE_NETWORK_ERROR,
                    "url": api_url,
                    "provider": "scrapfly",
                    "exception": e,
                }
            )
            return None, RESPONSE_NETWORK_ERROR

    def _scrape_with_decodo(self, token: str) -> Tuple[ResponseType | None, int]:
        """Scrape using Decodo Web Scraping API."""
        api_url = "https://scraper-api.decodo.com/v2/scrape"
        payload = {
            "target": "universal",
            "url": self.url,
            "headless": "html",
        }

        if self.wait_for_selector:
            payload["browser_actions"] = [
                {
                    "type": "wait_for_element",
                    "selector": {"type": "css", "value": self.wait_for_selector},
                    "timeout_s": 30,
                }
            ]

        headers = {"Authorization": f"Basic {token}"}

        try:
            response = requests.post(
                api_url, json=payload, headers=headers, timeout=self.timeout
            )

            if response.status_code == 200:
                data = response.json()
                content = data.get("results", [{}])[0].get("content", "")
                resp = ScraperResponse(self.url, content.encode("utf-8"))

                self.logs.append(
                    {
                        "response_type": RESPONSE_OK,
                        "url": api_url,
                        "provider": "decodo",
                        "exception": None,
                    }
                )
                return resp, RESPONSE_OK
            elif response.status_code == 429:
                logger.warning("Decodo quota exceeded")
                self.logs.append(
                    {
                        "response_type": RESPONSE_QUOTA_EXCEEDED,
                        "url": api_url,
                        "provider": "decodo",
                        "exception": response.text,
                    }
                )
                return None, RESPONSE_QUOTA_EXCEEDED
            else:
                self.logs.append(
                    {
                        "response_type": RESPONSE_NETWORK_ERROR,
                        "url": api_url,
                        "provider": "decodo",
                        "exception": f"HTTP {response.status_code}",
                    }
                )
                return None, RESPONSE_NETWORK_ERROR

        except Exception as e:
            logger.debug(f"Decodo error: {e}")
            self.logs.append(
                {
                    "response_type": RESPONSE_NETWORK_ERROR,
                    "url": api_url,
                    "provider": "decodo",
                    "exception": e,
                }
            )
            return None, RESPONSE_NETWORK_ERROR

    def _scrape_with_scraperapi(self, api_key: str) -> Tuple[ResponseType | None, int]:
        """Scrape using ScraperAPI."""
        params = {
            "api_key": api_key,
            "url": self.url,
            "render": "true",
        }
        if self.wait_for_selector:
            params["wait_for_selector"] = self.wait_for_selector

        api_url = f"https://api.scraperapi.com?{urlencode(params)}"

        try:
            response = requests.get(api_url, timeout=self.timeout)

            if response.status_code == 200:
                resp = ScraperResponse(
                    self.url, response.content, headers=dict(response.headers)
                )

                self.logs.append(
                    {
                        "response_type": RESPONSE_OK,
                        "url": api_url,
                        "provider": "scraperapi",
                        "exception": None,
                    }
                )
                return resp, RESPONSE_OK
            elif response.status_code == 429:
                logger.warning("ScraperAPI quota exceeded")
                self.logs.append(
                    {
                        "response_type": RESPONSE_QUOTA_EXCEEDED,
                        "url": api_url,
                        "provider": "scraperapi",
                        "exception": response.text,
                    }
                )
                return None, RESPONSE_QUOTA_EXCEEDED
            else:
                self.logs.append(
                    {
                        "response_type": RESPONSE_NETWORK_ERROR,
                        "url": api_url,
                        "provider": "scraperapi",
                        "exception": f"HTTP {response.status_code}",
                    }
                )
                return None, RESPONSE_NETWORK_ERROR

        except Exception as e:
            logger.debug(f"ScraperAPI error: {e}")
            self.logs.append(
                {
                    "response_type": RESPONSE_NETWORK_ERROR,
                    "url": api_url,
                    "provider": "scraperapi",
                    "exception": e,
                }
            )
            return None, RESPONSE_NETWORK_ERROR

    def _scrape_with_scrapingbee(self, api_key: str) -> Tuple[ResponseType | None, int]:
        """Scrape using ScrapingBee API."""
        params = {
            "api_key": api_key,
            "url": self.url,
            "render_js": "true",
            "premium_proxy": "true",
        }
        if self.wait_for_selector:
            params["wait_for"] = self.wait_for_selector

        api_url = f"https://app.scrapingbee.com/api/v1/?{urlencode(params)}"

        try:
            response = requests.get(api_url, timeout=self.timeout)

            if response.status_code == 200:
                resp = ScraperResponse(
                    self.url, response.content, headers=dict(response.headers)
                )

                self.logs.append(
                    {
                        "response_type": RESPONSE_OK,
                        "url": api_url,
                        "provider": "scrapingbee",
                        "exception": None,
                    }
                )
                return resp, RESPONSE_OK
            elif response.status_code == 429:
                logger.warning("ScrapingBee quota exceeded")
                self.logs.append(
                    {
                        "response_type": RESPONSE_QUOTA_EXCEEDED,
                        "url": api_url,
                        "provider": "scrapingbee",
                        "exception": response.text,
                    }
                )
                return None, RESPONSE_QUOTA_EXCEEDED
            else:
                self.logs.append(
                    {
                        "response_type": RESPONSE_NETWORK_ERROR,
                        "url": api_url,
                        "provider": "scrapingbee",
                        "exception": f"HTTP {response.status_code}",
                    }
                )
                return None, RESPONSE_NETWORK_ERROR

        except Exception as e:
            logger.debug(f"ScrapingBee error: {e}")
            self.logs.append(
                {
                    "response_type": RESPONSE_NETWORK_ERROR,
                    "url": api_url,
                    "provider": "scrapingbee",
                    "exception": e,
                }
            )
            return None, RESPONSE_NETWORK_ERROR

    def _scrape_with_custom(self, custom_url: str) -> Tuple[ResponseType | None, int]:
        """Scrape using custom URL template (backup provider)."""
        api_url = custom_url.replace("__URL__", quote(self.url, safe=""))
        if self.wait_for_selector:
            api_url = api_url.replace(
                "__SELECTOR__", quote(self.wait_for_selector, safe="")
            )

        try:
            response = requests.get(api_url, timeout=self.timeout)

            if response.status_code == 200:
                resp = ScraperResponse(
                    self.url, response.content, headers=dict(response.headers)
                )

                self.logs.append(
                    {
                        "response_type": RESPONSE_OK,
                        "url": api_url,
                        "provider": "custom",
                        "exception": None,
                    }
                )
                return resp, RESPONSE_OK
            else:
                self.logs.append(
                    {
                        "response_type": RESPONSE_NETWORK_ERROR,
                        "url": api_url,
                        "provider": "custom",
                        "exception": f"HTTP {response.status_code}",
                    }
                )
                return None, RESPONSE_NETWORK_ERROR

        except Exception as e:
            logger.debug(f"Custom provider error: {e}")
            self.logs.append(
                {
                    "response_type": RESPONSE_NETWORK_ERROR,
                    "url": api_url,
                    "provider": "custom",
                    "exception": e,
                }
            )
            return None, RESPONSE_NETWORK_ERROR

    def _scrape_with_provider(self, provider: str) -> Tuple[ResponseType | None, int]:
        """Scrape using the specified provider."""
        logger.debug(f"Fetching {self.url} with {provider}...")
        if provider == "scrapfly":
            api_key = settings.DOWNLOADER_SCRAPFLY_KEY
            if not api_key:
                logger.debug("DOWNLOADER_SCRAPFLY_KEY not configured")
                return None, RESPONSE_NETWORK_ERROR
            return self._scrape_with_scrapfly(api_key)

        elif provider == "decodo":
            token = settings.DOWNLOADER_DECODO_TOKEN
            if not token:
                logger.debug("DOWNLOADER_DECODO_TOKEN not configured")
                return None, RESPONSE_NETWORK_ERROR
            return self._scrape_with_decodo(token)

        elif provider == "scraperapi":
            api_key = settings.DOWNLOADER_SCRAPERAPI_KEY
            if not api_key:
                logger.debug("DOWNLOADER_SCRAPERAPI_KEY not configured")
                return None, RESPONSE_NETWORK_ERROR
            return self._scrape_with_scraperapi(api_key)

        elif provider == "scrapingbee":
            api_key = settings.DOWNLOADER_SCRAPINGBEE_KEY
            if not api_key:
                logger.debug("DOWNLOADER_SCRAPINGBEE_KEY not configured")
                return None, RESPONSE_NETWORK_ERROR
            return self._scrape_with_scrapingbee(api_key)

        elif provider == "custom":
            custom_url = settings.DOWNLOADER_CUSTOMSCRAPER_URL
            if not custom_url:
                logger.debug("DOWNLOADER_CUSTOMSCRAPER_URL not configured")
                return None, RESPONSE_NETWORK_ERROR
            return self._scrape_with_custom(custom_url)

        else:
            logger.error(f"Unknown provider: {provider}")
            return None, RESPONSE_NETWORK_ERROR

    def get_providers(self):
        """Get list of providers to try, from settings."""
        providers_str = settings.DOWNLOADER_PROVIDERS
        if not providers_str:
            return []
        return [p.strip() for p in providers_str.split(",") if p.strip()]

    def get_backup_provider(self):
        """Get the backup provider (custom scraper)."""
        return "custom" if settings.DOWNLOADER_CUSTOMSCRAPER_URL else None

    def download(self) -> ResponseType:
        """Download using configured scraping providers in order, with custom as backup."""
        # Fall back to BasicDownloader behavior when in mock mode or no providers configured
        if _mock_mode:
            return super().download()

        providers = self.get_providers()

        if not providers:
            # No scraping providers configured, fall back to basic download
            logger.debug("No scraping providers configured, using basic download")
            return super().download()

        resp = None
        resp_type = None
        last_try = False

        # Try each configured provider
        for provider in providers:
            resp, resp_type = self._scrape_with_provider(provider)

            if resp_type == RESPONSE_OK and resp is not None:
                self.response_type = resp_type
                return resp
            elif resp_type == RESPONSE_INVALID_CONTENT:
                # Don't retry on invalid content
                break
            elif resp_type == RESPONSE_CENSORSHIP:
                # Try backup provider for censored content
                break
            # Continue to next provider on network error or quota exceeded

        # Try backup provider if main providers failed
        backup = self.get_backup_provider()
        if backup and not last_try:
            logger.debug(f"Trying backup provider: {backup}")
            resp, resp_type = self._scrape_with_provider(backup)

        self.response_type = resp_type
        if self.response_type == RESPONSE_OK and resp:
            return resp
        else:
            raise DownloadError(self)
