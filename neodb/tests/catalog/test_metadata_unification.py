import pytest

from catalog.common.migrations import unify_metadata_20260715
from catalog.models import Album, Edition, Game, Movie, TVSeason, TVShow


class TestNormalizeLegacyVideoMetadata:
    def test_movie_full_legacy_shape(self):
        md = {
            "duration": "148分钟",
            "area": ["美国", "英国"],
            "showtime": [
                {"time": "2010-09-01", "region": "中国大陆"},
                {"time": "2010-07-16", "region": "美国"},
            ],
            "year": 2010,
        }
        Movie.normalize_legacy_metadata(md)
        assert md == {
            "length": 8880,
            "origin_country": ["US", "GB"],
            "release_date": "2010-07-16",
        }

    def test_movie_int_minutes_with_legacy_marker(self):
        # legacy TMDB shape: int minutes alongside year/showtime keys
        md = {"duration": 148, "year": 2010}
        Movie.normalize_legacy_metadata(md)
        assert md["length"] == 8880
        assert md["release_date"] == "2010"

    def test_movie_int_seconds_without_marker_untouched(self):
        # new-shape metadata: length in seconds is never re-inferred
        md = {"length": 480, "release_date": "2020-01-01"}
        Movie.normalize_legacy_metadata(md)
        assert md["length"] == 480

    def test_year_only_fallback(self):
        md = {"year": 1994}
        Movie.normalize_legacy_metadata(md)
        assert md == {"release_date": "1994"}

    def test_unparseable_showtime_kept(self):
        md = {"showtime": [{"time": "someday", "region": ""}]}
        Movie.normalize_legacy_metadata(md)
        assert md["showtime"] == [{"time": "someday", "region": ""}]
        assert "release_date" not in md

    def test_existing_release_date_wins(self):
        md = {"release_date": "2001-01-01", "year": 1999, "showtime": []}
        Movie.normalize_legacy_metadata(md)
        assert md["release_date"] == "2001-01-01"
        assert "year" not in md

    def test_tv_single_episode_length(self):
        md = {"single_episode_length": "45分钟", "year": 2018}
        TVShow.normalize_legacy_metadata(md)
        assert md["single_episode_length"] == 2700
        md2 = {"single_episode_length": 45, "area": []}
        TVSeason.normalize_legacy_metadata(md2)
        assert md2["single_episode_length"] == 2700

    def test_idempotent(self):
        md = {
            "duration": "8分钟",
            "area": ["美国"],
            "year": 2020,
        }
        Movie.normalize_legacy_metadata(md)
        converted = dict(md)
        assert converted["length"] == 480
        Movie.normalize_legacy_metadata(md)
        assert md == converted


class TestNormalizeLegacyAlbumMetadata:
    def test_full_legacy_shape(self):
        md = {
            "duration": 2368000,
            "media": "Audio CD",
            "album_type": "专辑",
            "release_date": "2015-02-23",
        }
        Album.normalize_legacy_metadata(md)
        assert md == {
            "length": 2368,
            "media_format": ["cd"],
            "album_type": ["album"],
            "release_date": "2015-02-23",
        }

    def test_seconds_untouched(self):
        md = {"length": 2368, "album_type": ["album"]}
        Album.normalize_legacy_metadata(md)
        assert md["length"] == 2368
        assert md["album_type"] == ["album"]

    def test_media_format_not_overwritten(self):
        md = {"media": "Vinyl", "media_format": ["cd"]}
        Album.normalize_legacy_metadata(md)
        assert md["media_format"] == ["cd"]
        assert "media" not in md

    def test_display_properties_tolerate_legacy_scalars(self):
        # templates iterate these; a pre-migration string value must not
        # be iterated character by character
        a = Album(metadata={"album_type": "专辑", "media_format": ["cd"]})
        assert a.display_album_types == ["album"]
        assert a.display_media_formats == ["cd"]


class TestNormalizeLegacyGameMetadata:
    def test_release_year_fallback(self):
        md = {"release_year": 1995}
        Game.normalize_legacy_metadata(md)
        assert md == {"release_date": "1995"}

    def test_release_year_ignored_when_date_present(self):
        md = {"release_year": 1995, "release_date": "1995-06-01"}
        Game.normalize_legacy_metadata(md)
        assert md == {"release_date": "1995-06-01"}

    def test_localized_steam_date(self):
        md = {"release_date": "18 kwietnia 2011"}
        Game.normalize_legacy_metadata(md)
        assert md == {"release_date": "2011-04-18"}


class TestNormalizeLegacyEditionMetadata:
    def test_price(self):
        md = {"price": "USD 26.00"}
        Edition.normalize_legacy_metadata(md)
        assert md["price"] == "USD 26.00"
        md = {"price": "450 NTD"}
        Edition.normalize_legacy_metadata(md)
        assert md["price"] == "TWD 450"
        md = {"price": "99 美元"}
        Edition.normalize_legacy_metadata(md)
        assert md["price"] == "USD 99"
        # ends with 元 so the CNY hint fires, but the 日元 alias wins
        md = {"price": "66日元"}
        Edition.normalize_legacy_metadata(md)
        assert md["price"] == "JPY 66"

    def test_yuan_suffix_assumed_cny(self):
        md = {"price": "19.00元"}
        Edition.normalize_legacy_metadata(md)
        assert md["price"] == "CNY 19.00"
        md = {"price": "1,299 元"}
        Edition.normalize_legacy_metadata(md)
        assert md["price"] == "CNY 1299"
        # annotated values still do not parse and are kept verbatim
        md = {"price": "48.00元（全二册）"}
        Edition.normalize_legacy_metadata(md)
        assert md["price"] == "48.00元（全二册）"

    def test_ambiguous_price_kept(self):
        # ¥ could be JPY or CNY; bare numbers have no currency at all
        for price in ("¥19.00", "￥484", "19.00"):
            md = {"price": price}
            Edition.normalize_legacy_metadata(md)
            assert md["price"] == price


@pytest.mark.django_db(databases="__all__")
class TestDerivedProperties:
    def test_movie_year(self):
        m = Movie(metadata={"release_date": "2010-07-16"})
        assert m.year == 2010
        assert Movie(metadata={}).year is None

    def test_game_release_year(self):
        g = Game(metadata={"release_date": "1995"})
        assert g.release_year == 1995

    def test_indexable_dates(self):
        m = Movie.objects.create(metadata={"release_date": "2010"})
        assert m.to_indexable_doc()["date"] == [20100000]
        m2 = Movie.objects.create(metadata={"release_date": "2010-09-10"})
        assert m2.to_indexable_doc()["date"] == [20100910]
        a = Album.objects.create(
            metadata={"release_date": "2014", "album_type": ["ep"]}
        )
        d = a.to_indexable_doc()
        assert d["date"] == [20140000]
        assert d["format"] == ["ep"]


@pytest.mark.django_db(databases="__all__")
class TestDeprecatedApiAliases:
    def test_movie_aliases(self):
        m = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Inception"}],
                "release_date": "2010-07-16",
                "length": 8880,
                "origin_country": ["US", "GB"],
                "site": "https://example.org/inception",
            }
        )
        o = m.ap_object
        assert o["year"] == 2010
        assert o["release_date"] == "2010-07-16"
        # duration keeps its legacy display-string shape for older
        # peers/clients; length carries the canonical seconds
        assert o["duration"] == "2h 28m"
        assert o["length"] == 8880
        assert o["origin_country"] == ["US", "GB"]
        assert o["area"] == ["US", "GB"]
        assert o["showtime"] == [{"time": "2010-07-16", "region": ""}]
        # official_site is aliased to the internal site attribute
        assert o["official_site"] == "https://example.org/inception"
        assert o["site"] == "https://example.org/inception"

    def test_movie_stale_legacy_metadata_tolerated(self):
        # pre-migration rows must not break schema validation; the legacy
        # duration key surfaces as null length until the migration runs
        m = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Old"}],
                "duration": "148分钟",
            }
        )
        o = m.ap_object
        assert o["length"] is None
        assert o["duration"] is None
        # a corrupt/hand-edited string in length still parses at read time
        m2 = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Odd"}],
                "length": "148分钟",
            }
        )
        assert m2.ap_object["length"] == 8880

    def test_album_media_alias(self):
        a = Album.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "X"}],
                "artist": ["Y"],
                "album_type": ["album"],
                "media_format": ["cd", "vinyl"],
                "length": 2368,
            }
        )
        o = a.ap_object
        assert o["album_type"] == ["album"]
        assert o["media_format"] == ["cd", "vinyl"]
        assert o["media"] == "cd, vinyl"
        # legacy shape stays milliseconds for older peers/clients
        assert o["duration"] == 2368000
        assert o["length"] == 2368

    def test_short_and_long_durations_not_recoerced_at_read_time(self):
        # unit inference happens only on legacy ingest; stored seconds are
        # trusted as-is by API/schema.org even near heuristic boundaries
        m = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Short"}],
                "length": 480,  # 8-minute short film
                "release_date": "2020",
            }
        )
        assert m.ap_object["length"] == 480
        assert m.to_schema_org()["duration"] == "PT0H8M"
        a = Album.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Box"}],
                "artist": ["Y"],
                "length": 55200,  # 15h20m box set
            }
        )
        assert a.ap_object["length"] == 55200
        assert a.ap_object["duration"] == 55200000

    def test_ap_object_round_trips_losslessly(self):
        # what one current server emits, another ingests without loss
        m = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Short"}],
                "length": 480,
                "release_date": "2020",
            }
        )
        o = m.ap_object
        assert o["release_date"] == "2020"
        assert o["duration"] == "8m"
        assert o["length"] == 480
        md = {k: v for k, v in o.items()}
        Movie.normalize_legacy_metadata(md)
        assert md["release_date"] == "2020"
        assert md["length"] == 480
        a = Album.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Box"}],
                "artist": ["Y"],
                "length": 55200,
                "release_date": "1997-05",
            }
        )
        oa = a.ap_object
        md = {k: v for k, v in oa.items()}
        Album.normalize_legacy_metadata(md)
        assert md["length"] == 55200
        assert md["release_date"] == "1997-05"

    def test_merge_preserves_legacy_year(self):
        src = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Src"}],
                "year": 2010,
            }
        )
        dst = Movie.objects.create(
            metadata={"localized_title": [{"lang": "en", "text": "Dst"}]}
        )
        src.merge_to(dst)
        dst.refresh_from_db()
        assert dst.metadata["release_date"] == "2010"
        assert dst.year == 2010

    def test_game_release_year_alias(self):
        g = Game.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "G"}],
                "release_date": "1995",
            }
        )
        o = g.ap_object
        assert o["release_date"] == "1995"
        assert o["release_year"] == 1995


@pytest.mark.django_db(databases="__all__")
class TestUnifyMetadataMigration:
    def test_migration_converts_and_is_idempotent(self):
        m = Movie.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "Inception"}],
                "duration": "148分钟",
                "area": ["美国"],
                "showtime": [{"time": "2010-07-16", "region": "美国"}],
                "year": 2010,
            }
        )
        a = Album.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "X"}],
                "artist": ["Y"],
                "duration": 2368000,
                "media": "Audio CD",
                "album_type": "专辑",
            }
        )
        g = Game.objects.create(
            metadata={
                "localized_title": [{"lang": "en", "text": "G"}],
                "release_year": 1995,
            }
        )
        unify_metadata_20260715()
        m.refresh_from_db()
        a.refresh_from_db()
        g.refresh_from_db()
        assert m.metadata["length"] == 8880
        assert m.metadata["origin_country"] == ["US"]
        assert m.metadata["release_date"] == "2010-07-16"
        assert "area" not in m.metadata
        assert "showtime" not in m.metadata
        assert "year" not in m.metadata
        assert a.metadata["length"] == 2368
        assert a.metadata["media_format"] == ["cd"]
        assert a.metadata["album_type"] == ["album"]
        assert "media" not in a.metadata
        assert g.metadata["release_date"] == "1995"
        assert "release_year" not in g.metadata

        before = (dict(m.metadata), dict(a.metadata), dict(g.metadata))
        unify_metadata_20260715()
        m.refresh_from_db()
        a.refresh_from_db()
        g.refresh_from_db()
        assert (dict(m.metadata), dict(a.metadata), dict(g.metadata)) == before
