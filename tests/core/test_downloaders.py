from unittest.mock import MagicMock

from catalog.common.downloaders import (
    RESPONSE_INVALID_CONTENT,
    RESPONSE_NETWORK_ERROR,
    RESPONSE_OK,
    RESPONSE_QUOTA_EXCEEDED,
    BasicDownloader,
    DownloadError,
    MockResponse,
    ScraperResponse,
    get_mock_file,
)


class TestGetMockFile:
    def test_basic_url(self):
        result = get_mock_file("https://example.com/page")
        assert result  # non-empty
        # no special characters remain
        assert "/" not in result
        assert ":" not in result

    def test_replaces_special_chars(self):
        result = get_mock_file("https://example.com/path?key=value&foo=bar")
        assert "/" not in result
        assert "?" not in result
        assert "&" not in result

    def test_truncates_long_urls(self):
        long_url = "https://example.com/" + "a" * 300
        result = get_mock_file(long_url)
        assert len(result) <= 255

    def test_replaces_api_keys(self):
        url = "https://api.example.com?key=SECRET123&other=value"
        result = get_mock_file(url)
        assert "SECRET123" not in result
        assert "key_8964" in result


class TestBasicDownloaderValidateResponse:
    def test_none_response(self):
        dl = BasicDownloader("https://example.com")
        assert dl.validate_response(None) == RESPONSE_NETWORK_ERROR

    def test_200_response(self):
        dl = BasicDownloader("https://example.com")
        resp = MagicMock()
        resp.status_code = 200
        assert dl.validate_response(resp) == RESPONSE_OK

    def test_429_response(self):
        dl = BasicDownloader("https://example.com")
        resp = MagicMock()
        resp.status_code = 429
        assert dl.validate_response(resp) == RESPONSE_QUOTA_EXCEEDED

    def test_404_response(self):
        dl = BasicDownloader("https://example.com")
        resp = MagicMock()
        resp.status_code = 404
        assert dl.validate_response(resp) == RESPONSE_INVALID_CONTENT

    def test_500_response(self):
        dl = BasicDownloader("https://example.com")
        resp = MagicMock()
        resp.status_code = 500
        assert dl.validate_response(resp) == RESPONSE_INVALID_CONTENT

    def test_301_response(self):
        dl = BasicDownloader("https://example.com")
        resp = MagicMock()
        resp.status_code = 301
        assert dl.validate_response(resp) == RESPONSE_INVALID_CONTENT


class TestDownloadError:
    def test_network_error_message(self):
        dl = BasicDownloader("https://example.com")
        dl.response_type = RESPONSE_NETWORK_ERROR
        err = DownloadError(dl)
        assert "Network Error" in err.message
        assert "https://example.com" in err.message

    def test_invalid_content_message(self):
        dl = BasicDownloader("https://example.com")
        dl.response_type = RESPONSE_INVALID_CONTENT
        err = DownloadError(dl)
        assert "Invalid Response" in err.message

    def test_quota_exceeded_message(self):
        dl = BasicDownloader("https://example.com")
        dl.response_type = RESPONSE_QUOTA_EXCEEDED
        err = DownloadError(dl)
        assert "API Quota Exceeded" in err.message

    def test_unknown_error_message(self):
        dl = BasicDownloader("https://example.com")
        dl.response_type = 999
        err = DownloadError(dl)
        assert "Unknown Error" in err.message

    def test_custom_msg_appended(self):
        dl = BasicDownloader("https://example.com")
        dl.response_type = RESPONSE_NETWORK_ERROR
        err = DownloadError(dl, "max retries")
        assert "max retries" in err.message


class TestScraperResponse:
    def test_text_property(self):
        resp = ScraperResponse("https://example.com", b"hello world")
        assert resp.text == "hello world"

    def test_json_method(self):
        resp = ScraperResponse("https://example.com", b'{"key": "value"}')
        assert resp.json() == {"key": "value"}

    def test_status_code_default(self):
        resp = ScraperResponse("https://example.com", b"content")
        assert resp.status_code == 200

    def test_custom_status_code(self):
        resp = ScraperResponse("https://example.com", b"content", status_code=404)
        assert resp.status_code == 404

    def test_default_headers(self):
        resp = ScraperResponse("https://example.com", b"content")
        assert resp.headers == {"content-type": "text/html"}

    def test_custom_headers(self):
        resp = ScraperResponse(
            "https://example.com",
            b"content",
            headers={"content-type": "application/json"},
        )
        assert resp.headers["content-type"] == "application/json"

    def test_html_method(self):
        resp = ScraperResponse(
            "https://example.com", b"<html><body><p>test</p></body></html>"
        )
        tree = resp.html()
        assert tree is not None

    def test_xml_method(self):
        resp = ScraperResponse("https://example.com", b"<root><item>test</item></root>")
        tree = resp.xml()
        assert tree is not None


class TestMockResponse:
    def test_nonexistent_file_returns_404(self):
        resp = MockResponse("https://nonexistent-url-for-test.com/page.jpg")
        assert resp.status_code == 404

    def test_headers_for_jpg(self):
        resp = MockResponse("https://example.com/image.jpg")
        assert resp.headers["content-type"] == "image/jpeg"

    def test_headers_for_html(self):
        resp = MockResponse("https://example.com/page.html")
        assert resp.headers["content-type"] == "text/html"
