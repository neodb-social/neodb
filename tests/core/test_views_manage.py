import pytest

from common.views_manage import AccessSettings, BrandingSettings, DiscoverSettings


class TestConvertValueList:
    """Test _convert_value for list-type fields."""

    @pytest.fixture(autouse=True)
    def setup_view(self):
        self.view = AccessSettings()

    def test_list_from_multiline_string(self):
        result = self.view._convert_value(
            "mastodon_login_whitelist", "example.com\nother.org\n"
        )
        assert result == ["example.com", "other.org"]

    def test_list_empty_string(self):
        result = self.view._convert_value("mastodon_login_whitelist", "")
        assert result == []

    def test_list_none_value(self):
        result = self.view._convert_value("mastodon_login_whitelist", None)
        assert result == []

    def test_list_strips_blank_lines(self):
        result = self.view._convert_value("mastodon_login_whitelist", "a\n\n  \nb\n")
        assert result == ["a", "b"]


class TestConvertValueDict:
    """Test _convert_value for dict-type fields."""

    @pytest.fixture(autouse=True)
    def setup_view(self):
        self.view = BrandingSettings()

    def test_dict_with_json_schema_returns_raw(self):
        raw = {"key": "value"}
        result = self.view._convert_value("site_links", raw)
        assert result == {"key": "value"}

    def test_dict_with_json_schema_none_returns_empty(self):
        result = self.view._convert_value("site_links", None)
        assert result == {}


class TestConvertValueSimple:
    """Test _convert_value for simple types (str, bool, int)."""

    @pytest.fixture(autouse=True)
    def setup_view(self):
        self.branding = BrandingSettings()
        self.access = AccessSettings()
        self.discover = DiscoverSettings()

    def test_passthrough_for_string(self):
        result = self.branding._convert_value("site_name", "My Site")
        assert result == "My Site"

    def test_passthrough_bool(self):
        result = self.access._convert_value("invite_only", True)
        assert result is True

    def test_passthrough_int(self):
        result = self.discover._convert_value("min_marks_for_discover", 5)
        assert result == 5
