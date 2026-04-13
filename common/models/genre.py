"""
Genre support utilities

Provides a canonical genre list for all media types (movies, TV, music, games,
podcasts, performances) with translatable labels, normalization from various
scraper sources, and alias-based matching.

Pattern follows common/models/lang.py for language support.
"""

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

# Canonical genre catalog: slug -> translatable label
# One flat list shared across all media types.
GENRE_CATALOG = {
    # Universal / cross-media
    "action": _("Action"),
    "adventure": _("Adventure"),
    "comedy": _("Comedy"),
    "drama": _("Drama"),
    "fantasy": _("Fantasy"),
    "horror": _("Horror"),
    "mystery": _("Mystery"),
    "romance": _("Romance"),
    "sci-fi": _("Sci-Fi"),
    "thriller": _("Thriller"),
    "animation": _("Animation"),
    "documentary": _("Documentary"),
    "family": _("Family"),
    "history": _("History"),
    "biographical": _("Biographical"),
    "war": _("War"),
    "western": _("Western"),
    "crime": _("Crime"),
    "sports": _("Sports"),
    "music": _("Music"),
    # Film/TV
    "film-noir": _("Film Noir"),
    "musical": _("Musical"),
    "reality": _("Reality"),
    "talk-show": _("Talk Show"),
    "news": _("News"),
    "game-show": _("Game Show"),
    "martial-arts": _("Martial Arts"),
    "period-drama": _("Period Drama"),
    "superhero": _("Superhero"),
    "disaster": _("Disaster"),
    "erotic": _("Erotic"),
    "short-film": _("Short Film"),
    "lgbtq": _("LGBTQ"),
    "tv-movie": _("TV Movie"),
    # Music
    "rock": _("Rock"),
    "pop": _("Pop"),
    "hip-hop": _("Hip Hop"),
    "electronic": _("Electronic"),
    "jazz": _("Jazz"),
    "classical": _("Classical"),
    "blues": _("Blues"),
    "country": _("Country"),
    "folk": _("Folk"),
    "r-and-b": _("R&B"),
    "metal": _("Metal"),
    "punk": _("Punk"),
    "soul": _("Soul"),
    "reggae": _("Reggae"),
    "latin": pgettext_lazy("genre", "Latin"),
    "world-music": _("World Music"),
    "ambient": _("Ambient"),
    "new-age": _("New Age"),
    "indie": _("Indie"),
    "alternative": _("Alternative"),
    "dance": _("Dance"),
    "funk": _("Funk"),
    "gospel": _("Gospel"),
    "soundtrack": _("Soundtrack"),
    "k-pop": _("K-Pop"),
    "easy-listening": _("Easy Listening"),
    # Game
    "rpg": _("RPG"),
    "strategy": _("Strategy"),
    "simulation": _("Simulation"),
    "racing": _("Racing"),
    "puzzle": _("Puzzle"),
    "platformer": _("Platformer"),
    "shooter": _("Shooter"),
    "fighting": _("Fighting"),
    "survival": _("Survival"),
    "sandbox": _("Sandbox"),
    "roguelike": _("Roguelike"),
    "visual-novel": _("Visual Novel"),
    "card-game": _("Card Game"),
    "board-game": _("Board Game"),
    "arcade": _("Arcade"),
    "mmo": _("MMO"),
    "moba": _("MOBA"),
    "pinball": _("Pinball"),
    "point-and-click": _("Point-and-Click"),
    "casual": _("Casual"),
    # Performance
    "opera": _("Opera"),
    "ballet": _("Ballet"),
    "theater": _("Theater"),
    "cabaret": _("Cabaret"),
    "xiqu": _("Xiqu"),
    # Podcast-relevant
    "true-crime": _("True Crime"),
    "self-help": _("Self-Help"),
    "business": _("Business"),
    "technology": _("Technology"),
    "education": _("Education"),
    "religion": _("Religion"),
    "leisure": _("Leisure"),
    "health": _("Health"),
}

GENRE_CHOICES = list(GENRE_CATALOG.items())
GENRE_CODES = dict(GENRE_CATALOG)


# Explicit mappings from scraped strings to canonical codes.
# If a value is not here and not in GENRE_CODES, it passes through as custom.
_SCRAPER_ALIASES: dict[str, str] = {
    # -----------------------------------------------------------
    # TMDB Movie genres
    # -----------------------------------------------------------
    "science fiction": "sci-fi",
    "tv movie": "tv-movie",
    # -----------------------------------------------------------
    # TMDB TV genres
    # -----------------------------------------------------------
    "kids": "family",
    "talk": "talk-show",
    # -----------------------------------------------------------
    # Douban Movie (Chinese)
    # -----------------------------------------------------------
    "剧情": "drama",
    "喜剧": "comedy",
    "动作": "action",
    "爱情": "romance",
    "科幻": "sci-fi",
    "动画": "animation",
    "悬疑": "mystery",
    "惊悚": "thriller",
    "恐怖": "horror",
    "纪录片": "documentary",
    "紀錄片": "documentary",
    "短片": "short-film",
    "情色": "erotic",
    "同性": "lgbtq",
    "音乐": "music",
    "歌舞": "musical",
    "家庭": "family",
    "儿童": "family",
    "传记": "biographical",
    "历史": "history",
    "战争": "war",
    "犯罪": "crime",
    "西部": "western",
    "奇幻": "fantasy",
    "冒险": "adventure",
    "灾难": "disaster",
    "武侠": "martial-arts",
    "古装": "period-drama",
    "运动": "sports",
    "黑色电影": "film-noir",
    "真人秀": "reality",
    "脱口秀": "talk-show",
    "鬼怪": "thriller",
    # -----------------------------------------------------------
    # Douban Music (Chinese)
    # -----------------------------------------------------------
    "摇滚": "rock",
    "流行": "pop",
    "民谣": "folk",
    "电子": "electronic",
    "说唱": "hip-hop",
    "爵士": "jazz",
    "古典": "classical",
    "蓝调": "blues",
    "乡村": "country",
    "轻音乐": "easy-listening",
    "世界音乐": "world-music",
    "拉丁": "latin",
    "朋克": "punk",
    "金属": "metal",
    "雷鬼": "reggae",
    "放克": "funk",
    "灵魂乐": "soul",
    "原声": "soundtrack",
    "新世纪": "new-age",
    # -----------------------------------------------------------
    # Douban Drama / Performance (Chinese)
    # -----------------------------------------------------------
    "话剧": "theater",
    "音乐剧": "musical",
    "歌剧": "opera",
    "舞蹈": "dance",
    "戏曲": "xiqu",
    # -----------------------------------------------------------
    # IGDB game genres
    # -----------------------------------------------------------
    "role-playing (rpg)": "rpg",
    "real time strategy (rts)": "strategy",
    "turn-based strategy (tbs)": "strategy",
    "point-and-click": "point-and-click",
    "simulator": "simulation",
    "sport": "sports",
    "platform": "platformer",
    "visual novel": "visual-novel",
    # -----------------------------------------------------------
    # Steam
    # -----------------------------------------------------------
    "massively multiplayer": "mmo",
    "quiz/trivia": "puzzle",
    # -----------------------------------------------------------
    # MusicBrainz / Spotify (only confident mappings)
    # -----------------------------------------------------------
    "rhythm and blues": "r-and-b",
    "r&b": "r-and-b",
    "rnb": "r-and-b",
    "hip hop": "hip-hop",
    "sci fi": "sci-fi",
    "science-fiction": "sci-fi",
    # -----------------------------------------------------------
    # Apple Podcast categories (only confident mappings)
    # -----------------------------------------------------------
    "health & fitness": "health",
    "kids & family": "family",
    "religion & spirituality": "religion",
    "true crime": "true-crime",
    "tv & film": "drama",
    # -----------------------------------------------------------
    # Bangumi (Japanese)
    # -----------------------------------------------------------
    "アクション": "action",
    "アドベンチャー": "adventure",
    "コメディ": "comedy",
    "ドラマ": "drama",
    "ファンタジー": "fantasy",
    "ホラー": "horror",
    "ミステリー": "mystery",
    "ロマンス": "romance",
    "シミュレーション": "simulation",
    "パズル": "puzzle",
    "シューティング": "shooter",
    "アニメーション": "animation",
    "ロールプレイング": "rpg",
    "レース": "racing",
    "スポーツ": "sports",
    "アーケード": "arcade",
    "格闘": "fighting",
}


def _build_genre_aliases() -> dict[str, str]:
    """
    Build a mapping of genre name aliases to their canonical slug code.
    Combines Django i18n translations of canonical labels with explicit
    scraper aliases.
    """
    aliases: dict[str, str] = {}

    # From Django translations: for each UI language, translate each
    # canonical genre label and map the lowercase form back to the slug.
    available_languages = settings.SUPPORTED_UI_LANGUAGES.keys()
    current_language = translation.get_language()
    for lang_code in available_languages:
        with translation.override(lang_code):
            for code, name in GENRE_CATALOG.items():
                name_str = str(name).lower().strip()
                if name_str and name_str != code:
                    aliases[name_str] = code
    if current_language:
        translation.activate(current_language)

    # Explicit scraper aliases (take precedence over i18n-derived ones)
    aliases.update(_SCRAPER_ALIASES)

    return aliases


_genre_aliases: dict[str, str] = _build_genre_aliases()

# Compound genres that expand to multiple canonical codes.
# Checked before single-code normalization in normalize_genres().
_COMPOUND_GENRES: dict[str, list[str]] = {
    "action & adventure": ["action", "adventure"],
    "sci-fi & fantasy": ["sci-fi", "fantasy"],
    "war & politics": ["war"],
}


def normalize_genre(genre: str) -> str | None:
    """Normalize a single genre string to a canonical code.

    Returns the canonical slug if a mapping exists, otherwise returns
    the input as-is (lowercased, stripped). Never returns None for
    non-empty input -- unknown genres pass through as custom values.
    """
    if not genre or not isinstance(genre, str):
        return None
    g = genre.strip()
    gl = g.lower()
    # Already a canonical code
    if gl in GENRE_CODES:
        return gl
    # Check aliases
    if gl in _genre_aliases:
        return _genre_aliases[gl]
    # Pass through as-is (preserve original casing for custom values)
    return g


def normalize_genres(genres: list[str]) -> list[str]:
    """Normalize a list of genre strings, removing duplicates and empty values.

    Compound genres (e.g. "Action & Adventure") are expanded to multiple codes.
    """
    result: list[str] = []
    for g in genres:
        if not g or not isinstance(g, str):
            continue
        gl = g.strip().lower()
        if gl in _COMPOUND_GENRES:
            result.extend(_COMPOUND_GENRES[gl])
        else:
            normalized = normalize_genre(g)
            if normalized:
                result.append(normalized)
    # Deduplicate while preserving order
    return list(dict.fromkeys(result))
