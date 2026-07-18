from django.utils import translation

from common.models.country import (
    country_display_name,
    normalize_countries,
    normalize_country,
)
from common.models.game_platform import (
    normalize_game_platform,
    normalize_game_platforms,
)

from common.models.duration import (
    coerce_album_duration,
    coerce_video_duration,
    format_duration,
    parse_duration_text,
)
from common.models.music_format import (
    normalize_album_types,
    normalize_media_formats,
)
from common.models.partial_date import (
    earliest_partial_date,
    parse_partial_date,
    partial_date_to_int,
    year_of_partial_date,
)
from common.models.price import normalize_price


class TestPartialDate:
    def test_parse(self):
        assert parse_partial_date("2014") == "2014"
        assert parse_partial_date("2010-9") == "2010-09"
        assert parse_partial_date("2010-9-5") == "2010-09-05"
        assert parse_partial_date("2010-09-05") == "2010-09-05"
        assert parse_partial_date("2025-01-25T00:36:35.000000000Z") == "2025-01-25"
        assert parse_partial_date("2010/09/05") == "2010-09-05"
        assert parse_partial_date(1995) == "1995"
        assert parse_partial_date(None) is None
        assert parse_partial_date("") is None
        assert parse_partial_date("garbage") is None
        assert parse_partial_date("18 kwietnia 2011") is None

    def test_parse_invalid_parts_truncate(self):
        assert parse_partial_date("2010-13") == "2010"
        assert parse_partial_date("2010-02-31") == "2010-02"
        assert parse_partial_date("2010-00-00") == "2010"

    def test_dates(self):
        from datetime import date, datetime

        assert parse_partial_date(date(2010, 9, 5)) == "2010-09-05"
        assert parse_partial_date(datetime(2010, 9, 5, 12, 0)) == "2010-09-05"

    def test_to_int(self):
        assert partial_date_to_int("2010") == 20100000
        assert partial_date_to_int("2010-09") == 20100900
        assert partial_date_to_int("2010-09-10") == 20100910
        assert partial_date_to_int(None) == 0
        assert partial_date_to_int("garbage") == 0

    def test_year(self):
        assert year_of_partial_date("2010-09-10") == 2010
        assert year_of_partial_date("2010") == 2010
        assert year_of_partial_date(None) is None

    def test_earliest(self):
        assert earliest_partial_date(["2010", "2010-09-10", None]) == "2010-09-10"
        assert earliest_partial_date(["2009", "2010-09-10"]) == "2009"
        assert earliest_partial_date(["2010-09", "2010-09-10"]) == "2010-09-10"
        assert earliest_partial_date([None, "x"]) is None
        assert earliest_partial_date([]) is None


class TestDuration:
    def test_parse_text(self):
        assert parse_duration_text("148分钟") == 8880
        assert parse_duration_text("2小时28分钟") == 8880
        assert parse_duration_text("45分") == 2700
        assert parse_duration_text("148 min") == 8880
        assert parse_duration_text("2h 14m") == 8040
        assert parse_duration_text("1:30:00") == 5400
        assert parse_duration_text("4:44") == 284
        assert parse_duration_text("PT2H28M") == 8880
        assert parse_duration_text("148") == 8880  # bare digits = minutes
        assert parse_duration_text("148分钟(导演剪辑版)") == 8880
        assert parse_duration_text("") is None
        assert parse_duration_text("garbage") is None

    def test_coerce_video(self):
        assert coerce_video_duration(148) == 8880  # legacy minutes
        assert coerce_video_duration(8880) == 8880  # already seconds
        assert coerce_video_duration("45分钟") == 2700
        assert coerce_video_duration(None) is None
        assert coerce_video_duration(0) is None

    def test_coerce_album(self):
        assert coerce_album_duration(2368000) == 2368  # legacy milliseconds
        assert coerce_album_duration(2368) == 2368  # already seconds
        assert coerce_album_duration("2368000") == 2368
        assert coerce_album_duration(None) is None

    def test_format(self):
        assert format_duration(8040) == "2h 14m"
        assert format_duration(7200) == "2h"
        assert format_duration(2700) == "45m"
        assert format_duration(58) == "58s"
        assert format_duration(0) == ""
        assert format_duration(None) == ""


class TestPrice:
    def test_canonical_unchanged(self):
        assert normalize_price("CNY 26") == "CNY 26"
        assert normalize_price("USD 10.99") == "USD 10.99"

    def test_prefixed(self):
        assert normalize_price("USD 26.00") == "USD 26.00"
        assert normalize_price("￥484", "JPY") == "JPY 484"

    def test_suffixed(self):
        assert normalize_price("19.00元", "CNY") == "CNY 19.00"
        assert normalize_price("450 NTD") == "TWD 450"
        assert normalize_price("26.00 USD") == "USD 26.00"
        assert normalize_price("1,299 JPY") == "JPY 1299"

    def test_chinese_currency_names(self):
        assert normalize_price("99 美元") == "USD 99"
        assert normalize_price("66日元") == "JPY 66"
        assert normalize_price("30 港币") == "HKD 30"
        assert normalize_price("9.9人民币") == "CNY 9.9"
        assert normalize_price("120 新台币") == "TWD 120"
        assert normalize_price("484円") == "JPY 484"
        # unknown CJK words resolve to no currency and are kept,
        # even when a source hint is present
        assert normalize_price("99 金币") == "99 金币"
        assert normalize_price("99 金币", "CNY") == "99 金币"

    def test_bare_number_needs_hint(self):
        assert normalize_price("5.99", "CNY") == "CNY 5.99"
        assert normalize_price("5.99") == "5.99"

    def test_ambiguous_unchanged_without_hint(self):
        assert normalize_price("￥484") == "￥484"
        assert normalize_price("19.00元") == "19.00元"

    def test_unparseable_unchanged(self):
        assert normalize_price("48.00元（全二册）") == "48.00元（全二册）"
        assert normalize_price("USD 26.00 / GBP 20") == "USD 26.00 / GBP 20"


class TestCountry:
    def test_codes(self):
        assert normalize_country("US") == "US"
        assert normalize_country("us") == "US"

    def test_douban_aliases(self):
        assert normalize_country("美国") == "US"
        assert normalize_country("中国大陆") == "CN"
        assert normalize_country("中国香港") == "HK"
        assert normalize_country("英国") == "GB"

    def test_english_names(self):
        assert normalize_country("United States") == "US"
        assert normalize_country("Japan") == "JP"

    def test_passthrough(self):
        assert normalize_country("西德") == "西德"
        assert normalize_country("苏联") == "苏联"

    def test_list(self):
        assert normalize_countries(["美国", "英国", "美国"]) == ["US", "GB"]
        assert normalize_countries("美国") == ["US"]
        assert normalize_countries(None) == []

    def test_display_name(self):
        assert country_display_name("US") == "United States"
        assert country_display_name("KR") == "South Korea"
        assert country_display_name("西德") == "西德"
        with translation.override("zh-hans"):
            assert country_display_name("US") == "美国"
            assert country_display_name("KR") == "韩国"


class TestGamePlatform:
    def test_single(self):
        assert normalize_game_platform("PC") == "windows"
        assert normalize_game_platform("PC (Microsoft Windows)") == "windows"
        assert normalize_game_platform("Macintosh") == "mac"
        assert normalize_game_platform("Nintendo Switch") == "switch"
        assert normalize_game_platform("NS") == "switch"
        assert normalize_game_platform("PlayStation 4") == "ps4"
        assert normalize_game_platform("Xbox Series X|S") == "xbox-series"
        assert normalize_game_platform("Boardgame") == "boardgame"
        assert normalize_game_platform("桌游") == "boardgame"
        assert normalize_game_platform("HTML5") == "web"
        assert normalize_game_platform("Sinclair ZX81") == "Sinclair ZX81"

    def test_list(self):
        assert normalize_game_platforms(["PC", "PS5", "Nintendo Switch"]) == [
            "windows",
            "ps5",
            "switch",
        ]
        assert normalize_game_platforms("PC / PS4") == ["windows", "ps4"]
        assert normalize_game_platforms(["windows", "PC"]) == ["windows"]
        assert normalize_game_platforms(None) == []


class TestMusicFormat:
    def test_album_types(self):
        assert normalize_album_types("专辑") == ["album"]
        assert normalize_album_types("EP") == ["ep"]
        assert normalize_album_types("Album, EP") == ["album", "ep"]
        assert normalize_album_types(["Mixtape/Street"]) == ["mixtape"]
        assert normalize_album_types("Custom Thing") == ["Custom Thing"]
        assert normalize_album_types(None) == []
        assert normalize_album_types(["album"]) == ["album"]

    def test_media_formats(self):
        assert normalize_media_formats("Audio CD") == ["cd"]
        assert normalize_media_formats("黑胶") == ["vinyl"]
        assert normalize_media_formats(["Vinyl", "LP"]) == ["vinyl"]
        assert normalize_media_formats("Digital Media") == ["digital"]
        assert normalize_media_formats(["cd"]) == ["cd"]
        assert normalize_media_formats(None) == []
