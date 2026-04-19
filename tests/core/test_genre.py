import pytest
from django.utils import translation

from common.models.genre import (
    GENRE_CATALOG,
    GENRE_CHOICES,
    GENRE_CODES,
    _build_genre_aliases,
    normalize_genre,
    normalize_genres,
)


@pytest.mark.django_db(databases="__all__")
class TestGenreCatalog:
    def test_genre_catalog_not_empty(self):
        assert len(GENRE_CATALOG) > 80

    def test_genre_choices_matches_catalog(self):
        assert len(GENRE_CHOICES) == len(GENRE_CATALOG)
        for code, label in GENRE_CHOICES:
            assert code in GENRE_CATALOG

    def test_genre_codes_matches_catalog(self):
        assert GENRE_CODES == GENRE_CATALOG


@pytest.mark.django_db(databases="__all__")
class TestNormalizeGenre:
    def test_empty_input(self):
        assert normalize_genre("") is None

    def test_canonical_codes_pass_through(self):
        assert normalize_genre("action") == "action"
        assert normalize_genre("sci-fi") == "sci-fi"
        assert normalize_genre("rpg") == "rpg"
        assert normalize_genre("hip-hop") == "hip-hop"
        assert normalize_genre("r-and-b") == "r-and-b"

    def test_canonical_codes_case_insensitive(self):
        assert normalize_genre("Action") == "action"
        assert normalize_genre("COMEDY") == "comedy"
        assert normalize_genre("Sci-Fi") == "sci-fi"
        assert normalize_genre("RPG") == "rpg"

    def test_custom_values_pass_through(self):
        """Unknown genres should pass through preserving original casing."""
        assert (
            normalize_genre("Hack and slash/Beat 'em up")
            == "Hack and slash/Beat 'em up"
        )
        assert normalize_genre("Card & Board Game") == "Card & Board Game"
        assert normalize_genre("Some Custom Genre") == "Some Custom Genre"
        assert normalize_genre("默剧") == "默剧"
        assert normalize_genre("多媒体") == "多媒体"

    # ----- TMDB Movie genres -----

    def test_tmdb_movie_genres(self):
        assert normalize_genre("Action") == "action"
        assert normalize_genre("Adventure") == "adventure"
        assert normalize_genre("Animation") == "animation"
        assert normalize_genre("Comedy") == "comedy"
        assert normalize_genre("Crime") == "crime"
        assert normalize_genre("Documentary") == "documentary"
        assert normalize_genre("Drama") == "drama"
        assert normalize_genre("Family") == "family"
        assert normalize_genre("Fantasy") == "fantasy"
        assert normalize_genre("History") == "history"
        assert normalize_genre("Horror") == "horror"
        assert normalize_genre("Music") == "music"
        assert normalize_genre("Mystery") == "mystery"
        assert normalize_genre("Romance") == "romance"
        assert normalize_genre("Science Fiction") == "sci-fi"
        assert normalize_genre("TV Movie") == "tv-movie"
        assert normalize_genre("Thriller") == "thriller"
        assert normalize_genre("War") == "war"
        assert normalize_genre("Western") == "western"

    # ----- TMDB TV genres -----

    def test_tmdb_tv_simple_genres(self):
        assert normalize_genre("Kids") == "family"
        assert normalize_genre("Talk") == "talk-show"

    # ----- Douban Movie genres (Chinese) -----

    def test_douban_movie_genres(self):
        assert normalize_genre("剧情") == "drama"
        assert normalize_genre("喜剧") == "comedy"
        assert normalize_genre("动作") == "action"
        assert normalize_genre("爱情") == "romance"
        assert normalize_genre("科幻") == "sci-fi"
        assert normalize_genre("动画") == "animation"
        assert normalize_genre("悬疑") == "mystery"
        assert normalize_genre("惊悚") == "thriller"
        assert normalize_genre("恐怖") == "horror"
        assert normalize_genre("纪录片") == "documentary"
        assert normalize_genre("紀錄片") == "documentary"
        assert normalize_genre("短片") == "short-film"
        assert normalize_genre("情色") == "erotic"
        assert normalize_genre("同性") == "lgbtq"
        assert normalize_genre("音乐") == "music"
        assert normalize_genre("歌舞") == "musical"
        assert normalize_genre("家庭") == "family"
        assert normalize_genre("儿童") == "family"
        assert normalize_genre("传记") == "biographical"
        assert normalize_genre("历史") == "history"
        assert normalize_genre("战争") == "war"
        assert normalize_genre("犯罪") == "crime"
        assert normalize_genre("西部") == "western"
        assert normalize_genre("奇幻") == "fantasy"
        assert normalize_genre("冒险") == "adventure"
        assert normalize_genre("灾难") == "disaster"
        assert normalize_genre("武侠") == "martial-arts"
        assert normalize_genre("古装") == "period-drama"
        assert normalize_genre("运动") == "sports"
        assert normalize_genre("黑色电影") == "film-noir"
        assert normalize_genre("真人秀") == "reality"
        assert normalize_genre("脱口秀") == "talk-show"
        assert normalize_genre("鬼怪") == "thriller"

    # ----- Douban Music genres (Chinese) -----

    def test_douban_music_genres(self):
        assert normalize_genre("摇滚") == "rock"
        assert normalize_genre("流行") == "pop"
        assert normalize_genre("民谣") == "folk"
        assert normalize_genre("电子") == "electronic"
        assert normalize_genre("说唱") == "hip-hop"
        assert normalize_genre("爵士") == "jazz"
        assert normalize_genre("古典") == "classical"
        assert normalize_genre("蓝调") == "blues"
        assert normalize_genre("乡村") == "country"
        assert normalize_genre("轻音乐") == "easy-listening"
        assert normalize_genre("世界音乐") == "world-music"
        assert normalize_genre("拉丁") == "latin"
        assert normalize_genre("朋克") == "punk"
        assert normalize_genre("金属") == "metal"
        assert normalize_genre("雷鬼") == "reggae"
        assert normalize_genre("放克") == "funk"
        assert normalize_genre("灵魂乐") == "soul"
        assert normalize_genre("原声") == "soundtrack"
        assert normalize_genre("新世纪") == "new-age"

    # ----- Douban Drama genres (Chinese) -----

    def test_douban_drama_genres(self):
        assert normalize_genre("话剧") == "huaju"
        assert normalize_genre("音乐剧") == "musical"
        assert normalize_genre("歌剧") == "opera"
        assert normalize_genre("舞蹈") == "dance"
        assert normalize_genre("戏曲") == "xiqu"
        assert normalize_genre("默剧") == "默剧"
        assert normalize_genre("多媒体") == "多媒体"

    # ----- IGDB game genres -----

    def test_igdb_genres(self):
        assert normalize_genre("Role-playing (RPG)") == "rpg"
        assert normalize_genre("Real Time Strategy (RTS)") == "strategy"
        assert normalize_genre("Turn-based strategy (TBS)") == "strategy"
        assert normalize_genre("Point-and-click") == "point-and-click"
        assert normalize_genre("Simulator") == "simulation"
        assert normalize_genre("Sport") == "sports"
        assert normalize_genre("Platform") == "platformer"
        assert normalize_genre("Shooter") == "shooter"
        assert normalize_genre("Fighting") == "fighting"
        assert normalize_genre("Puzzle") == "puzzle"
        assert normalize_genre("Racing") == "racing"
        assert normalize_genre("Adventure") == "adventure"
        assert normalize_genre("Arcade") == "arcade"
        assert normalize_genre("Visual Novel") == "visual-novel"
        assert normalize_genre("Indie") == "indie"
        assert normalize_genre("MOBA") == "moba"
        assert normalize_genre("Pinball") == "pinball"
        assert normalize_genre("Strategy") == "strategy"
        # These pass through as custom
        assert (
            normalize_genre("Hack and slash/Beat 'em up")
            == "Hack and slash/Beat 'em up"
        )
        assert normalize_genre("Card & Board Game") == "Card & Board Game"
        assert normalize_genre("Tactical") == "Tactical"
        assert normalize_genre("Quiz/Trivia") == "puzzle"

    # ----- Steam genres -----

    def test_steam_genres(self):
        assert normalize_genre("Massively Multiplayer") == "mmo"
        # Non-genre tags pass through as custom
        assert normalize_genre("Early Access") == "Early Access"
        assert normalize_genre("Free to Play") == "Free to Play"
        assert normalize_genre("Casual") == "casual"

    # ----- MusicBrainz / Spotify -----

    def test_musicbrainz_genres(self):
        assert normalize_genre("rhythm and blues") == "r-and-b"
        assert normalize_genre("r&b") == "r-and-b"
        assert normalize_genre("rnb") == "r-and-b"
        assert normalize_genre("hip hop") == "hip-hop"
        # Sub-genres pass through as custom
        assert normalize_genre("death metal") == "death metal"
        assert normalize_genre("indie rock") == "indie rock"
        assert normalize_genre("dream pop") == "dream pop"

    # ----- Apple Podcast categories -----

    def test_apple_podcast_categories(self):
        assert normalize_genre("Health & Fitness") == "health"
        assert normalize_genre("Kids & Family") == "family"
        assert normalize_genre("Religion & Spirituality") == "religion"
        assert normalize_genre("True Crime") == "true-crime"
        assert normalize_genre("TV & Film") == "drama"
        # These pass through as custom
        assert normalize_genre("Arts") == "Arts"
        assert normalize_genre("Government") == "Government"
        assert normalize_genre("Society & Culture") == "Society & Culture"

    # ----- Bangumi Japanese genres -----

    def test_bangumi_genres(self):
        assert normalize_genre("アクション") == "action"
        assert normalize_genre("アドベンチャー") == "adventure"
        assert normalize_genre("コメディ") == "comedy"
        assert normalize_genre("ドラマ") == "drama"
        assert normalize_genre("ファンタジー") == "fantasy"
        assert normalize_genre("ホラー") == "horror"
        assert normalize_genre("ミステリー") == "mystery"
        assert normalize_genre("ロマンス") == "romance"
        assert normalize_genre("シミュレーション") == "simulation"
        assert normalize_genre("パズル") == "puzzle"
        assert normalize_genre("シューティング") == "shooter"
        assert normalize_genre("アニメーション") == "animation"
        assert normalize_genre("ロールプレイング") == "rpg"
        assert normalize_genre("レース") == "racing"
        assert normalize_genre("スポーツ") == "sports"
        assert normalize_genre("アーケード") == "arcade"
        assert normalize_genre("格闘") == "fighting"


@pytest.mark.django_db(databases="__all__")
class TestNormalizeGenres:
    def test_empty_list(self):
        assert normalize_genres([]) == []

    def test_already_canonical_codes(self):
        assert normalize_genres(["action", "comedy", "drama"]) == [
            "action",
            "comedy",
            "drama",
        ]

    def test_mixed_input(self):
        assert normalize_genres(["Action", "科幻", "rock", "Some Custom"]) == [
            "action",
            "sci-fi",
            "rock",
            "Some Custom",
        ]

    def test_compound_genres_expanded(self):
        """TMDB compound TV genres should expand to multiple codes."""
        assert normalize_genres(["Action & Adventure"]) == ["action", "adventure"]
        assert normalize_genres(["Sci-Fi & Fantasy"]) == ["sci-fi", "fantasy"]
        assert normalize_genres(["War & Politics"]) == ["war"]

    def test_compound_genres_deduplicated(self):
        """Compound expansion should not create duplicates."""
        assert normalize_genres(["Action & Adventure", "Action"]) == [
            "action",
            "adventure",
        ]
        assert normalize_genres(["Sci-Fi & Fantasy", "Fantasy"]) == [
            "sci-fi",
            "fantasy",
        ]

    def test_deduplication(self):
        """Duplicates after normalization should be removed."""
        assert normalize_genres(["Action", "action", "动作"]) == ["action"]
        assert normalize_genres(["Science Fiction", "sci-fi", "科幻"]) == ["sci-fi"]

    def test_empty_strings_filtered(self):
        assert normalize_genres(["action", "", " ", "comedy"]) == [
            "action",
            "comedy",
        ]

    def test_preserves_order(self):
        assert normalize_genres(["comedy", "action", "drama"]) == [
            "comedy",
            "action",
            "drama",
        ]

    def test_full_tmdb_movie_genre_list(self):
        """Simulate a full TMDB movie genre list being normalized."""
        tmdb_genres = [
            "Action",
            "Adventure",
            "Animation",
            "Comedy",
            "Crime",
            "Documentary",
            "Drama",
            "Family",
            "Fantasy",
            "History",
            "Horror",
            "Music",
            "Mystery",
            "Romance",
            "Science Fiction",
            "Thriller",
            "War",
            "Western",
        ]
        result = normalize_genres(tmdb_genres)
        assert result == [
            "action",
            "adventure",
            "animation",
            "comedy",
            "crime",
            "documentary",
            "drama",
            "family",
            "fantasy",
            "history",
            "horror",
            "music",
            "mystery",
            "romance",
            "sci-fi",
            "thriller",
            "war",
            "western",
        ]

    def test_douban_movie_typical_input(self):
        """Typical Douban movie genre list."""
        assert normalize_genres(["剧情", "喜剧", "爱情"]) == [
            "drama",
            "comedy",
            "romance",
        ]

    def test_douban_music_typical_input(self):
        """Typical Douban music genre list."""
        assert normalize_genres(["摇滚", "流行", "电子"]) == [
            "rock",
            "pop",
            "electronic",
        ]

    def test_igdb_typical_input(self):
        """IGDB genre list for a game like Portal 2."""
        assert normalize_genres(["Shooter", "Platform", "Puzzle", "Adventure"]) == [
            "shooter",
            "platformer",
            "puzzle",
            "adventure",
        ]

    def test_mixed_custom_and_canonical(self):
        """Mix of normalizable and custom values."""
        assert normalize_genres(["Action", "Hack and slash/Beat 'em up", "RPG"]) == [
            "action",
            "Hack and slash/Beat 'em up",
            "rpg",
        ]


@pytest.mark.django_db(databases="__all__")
class TestBuildGenreAliases:
    def test_aliases_not_empty(self):
        aliases = _build_genre_aliases()
        assert len(aliases) > 50

    def test_aliases_include_scraper_entries(self):
        aliases = _build_genre_aliases()
        assert aliases.get("science fiction") == "sci-fi"
        assert aliases.get("剧情") == "drama"
        assert aliases.get("role-playing (rpg)") == "rpg"
        assert aliases.get("アクション") == "action"

    def test_aliases_preserves_current_language(self):
        original_language = translation.get_language()
        _build_genre_aliases()
        current_after = translation.get_language()
        assert original_language == current_after

    def test_aliases_include_i18n_translations(self):
        """Aliases should include translated labels from supported UI languages."""
        aliases = _build_genre_aliases()
        # Translated labels from non-English locales should map to codes
        # (English "action" == code "action" so it's skipped, but other
        # languages' translations should be present)
        action_aliases = [a for a, c in aliases.items() if c == "action"]
        assert len(action_aliases) > 0


@pytest.mark.django_db(databases="__all__")
class TestGenreNormalizationInModel:
    """Test that genre normalization works through the model save pipeline."""

    def test_normalize_metadata_normalizes_genres(self):
        from catalog.models import Movie

        movie = Movie.objects.create(
            title="Test Movie",
            localized_title=[{"lang": "en", "text": "Test Movie"}],
        )
        movie.genre = ["Science Fiction", "动作", "comedy"]
        changed = movie._normalize_genres()
        assert changed is True
        assert movie.genre == ["sci-fi", "action", "comedy"]

    def test_normalize_metadata_no_change_for_canonical(self):
        from catalog.models import Movie

        movie = Movie.objects.create(
            title="Test Movie 2",
            localized_title=[{"lang": "en", "text": "Test Movie 2"}],
        )
        movie.genre = ["action", "comedy", "drama"]
        changed = movie._normalize_genres()
        assert changed is False
        assert movie.genre == ["action", "comedy", "drama"]

    def test_normalize_metadata_preserves_custom(self):
        from catalog.models import Movie

        movie = Movie.objects.create(
            title="Test Movie 3",
            localized_title=[{"lang": "en", "text": "Test Movie 3"}],
        )
        movie.genre = ["Action", "Some Custom Genre", "默剧"]
        changed = movie._normalize_genres()
        assert changed is True
        assert movie.genre == ["action", "Some Custom Genre", "默剧"]

    def test_normalize_metadata_deduplicates(self):
        from catalog.models import Movie

        movie = Movie.objects.create(
            title="Test Movie 4",
            localized_title=[{"lang": "en", "text": "Test Movie 4"}],
        )
        movie.genre = ["Action", "action", "动作"]
        changed = movie._normalize_genres()
        assert changed is True
        assert movie.genre == ["action"]

    def test_normalize_metadata_called_from_normalize_metadata(self):
        from catalog.models import Movie

        movie = Movie.objects.create(
            title="Test Movie 5",
            localized_title=[{"lang": "en", "text": "Test Movie 5"}],
        )
        movie.genre = ["Science Fiction", "Comedy"]
        changed = movie.normalize_metadata()
        assert changed is True
        assert movie.genre == ["sci-fi", "comedy"]

    def test_game_genre_normalization(self):
        from catalog.models import Game

        game = Game.objects.create(
            title="Test Game",
            localized_title=[{"lang": "en", "text": "Test Game"}],
        )
        game.genre = ["Role-playing (RPG)", "Shooter", "Platform"]
        changed = game._normalize_genres()
        assert changed is True
        assert game.genre == ["rpg", "shooter", "platformer"]

    def test_album_genre_normalization(self):
        from catalog.models import Album

        album = Album.objects.create(
            title="Test Album",
            localized_title=[{"lang": "en", "text": "Test Album"}],
            artist=["Test Artist"],
        )
        album.genre = ["摇滚", "流行"]
        changed = album._normalize_genres()
        assert changed is True
        assert album.genre == ["rock", "pop"]

    def test_podcast_genre_normalization(self):
        from catalog.models import Podcast

        podcast = Podcast.objects.create(
            title="Test Podcast",
            localized_title=[{"lang": "en", "text": "Test Podcast"}],
        )
        podcast.genre = ["True Crime", "Health & Fitness"]
        changed = podcast._normalize_genres()
        assert changed is True
        assert podcast.genre == ["true-crime", "health"]

    def test_performance_genre_normalization(self):
        from catalog.models import Performance

        performance = Performance.objects.create(
            title="Test Performance",
            localized_title=[{"lang": "en", "text": "Test Performance"}],
        )
        performance.genre = ["话剧", "音乐剧"]
        changed = performance._normalize_genres()
        assert changed is True
        assert performance.genre == ["huaju", "musical"]


@pytest.mark.django_db(databases="__all__")
class TestFederatedItemGenreNormalization:
    """Test that genres from federated items are normalized during import."""

    def test_merge_normalizes_genres(self):
        from catalog.models import ExternalResource, IdType, Movie

        movie = Movie.objects.create(
            title="Federated Movie",
            localized_title=[{"lang": "en", "text": "Federated Movie"}],
        )
        resource = ExternalResource.objects.create(
            item=movie,
            id_type=IdType.Fediverse,
            id_value="https://remote.example/movie/123",
            url="https://remote.example/movie/123",
        )
        # Simulate federated metadata with non-normalized genres
        resource.metadata = {
            "title": "Federated Movie",
            "genre": ["Science Fiction", "Action", "动作"],
        }
        resource.save()
        movie.merge_data_from_external_resource(resource, ignore_existing_content=True)
        # After merge, genres should be normalized and deduplicated
        assert movie.genre == ["sci-fi", "action"]
