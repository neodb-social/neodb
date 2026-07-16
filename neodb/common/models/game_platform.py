"""
Game platform support

Canonical vocabulary for Game.platform, based on IGDB's platform
database (https://api-docs.igdb.com/#platform: each platform has a
name, abbreviation and slug, categorized as console / arcade /
platform / operating_system / portable_console / computer). Slugs
below follow IGDB's, cleaned up where IGDB's own slug is awkward
(e.g. "ps4--1", "genesis-slash-megadrive"); long-tail platforms pass
through as custom strings rather than being enumerated. "boardgame"
is ours (BGG); IGDB does not cover tabletop games.

Pattern follows common/models/genre.py.
"""

import re

from django.utils.translation import pgettext_lazy

GAME_PLATFORM_CATALOG = {
    # operating systems / computers
    "windows": "Windows",
    "mac": "macOS",
    "linux": "Linux",
    "dos": "DOS",
    "amiga": "Amiga",
    # mobile / web
    "ios": "iOS",
    "android": "Android",
    "web": pgettext_lazy("platform", "Web"),
    # Sony
    "ps1": "PlayStation",
    "ps2": "PlayStation 2",
    "ps3": "PlayStation 3",
    "ps4": "PlayStation 4",
    "ps5": "PlayStation 5",
    "psp": "PSP",
    "psvita": "PlayStation Vita",
    # Microsoft
    "xbox": "Xbox",
    "xbox360": "Xbox 360",
    "xboxone": "Xbox One",
    "xbox-series": "Xbox Series X|S",
    # Nintendo
    "switch": "Nintendo Switch",
    "switch-2": "Nintendo Switch 2",
    "wii": "Wii",
    "wiiu": "Wii U",
    "n64": "Nintendo 64",
    "gamecube": "GameCube",
    "nes": "NES",
    "snes": "SNES",
    "gb": "Game Boy",
    "gbc": "Game Boy Color",
    "gba": "Game Boy Advance",
    "nds": "Nintendo DS",
    "3ds": "Nintendo 3DS",
    # Sega
    "dreamcast": "Dreamcast",
    "saturn": "Sega Saturn",
    "genesis": "Genesis/Mega Drive",
    # other
    "arcade": pgettext_lazy("platform", "Arcade"),
    "boardgame": pgettext_lazy("platform", "Board Game"),
}

GAME_PLATFORM_CHOICES = list(GAME_PLATFORM_CATALOG.items())
GAME_PLATFORM_CODES = dict(GAME_PLATFORM_CATALOG)

# Scraped value -> slug. Keys must be lowercase. Sources: IGDB names
# and abbreviations, Steam ("PC"), itch.io, MobyGames JSON-LD names,
# Douban game platform tags, BGG.
_PLATFORM_ALIASES: dict[str, str] = {
    # operating systems
    "pc": "windows",
    "pc (microsoft windows)": "windows",
    "microsoft windows": "windows",
    "win": "windows",
    "macintosh": "mac",
    "macos": "mac",
    "mac os": "mac",
    "os x": "mac",
    # web
    "browser": "web",
    "web browser": "web",
    "html5": "web",
    "网页": "web",
    # Sony
    "playstation": "ps1",
    "psx": "ps1",
    "ps": "ps1",
    "playstation 2": "ps2",
    "playstation 3": "ps3",
    "playstation 4": "ps4",
    "playstation 5": "ps5",
    "playstation portable": "psp",
    "playstation vita": "psvita",
    "ps vita": "psvita",
    "psv": "psvita",
    # Microsoft
    "xbox series x|s": "xbox-series",
    "xbox series x": "xbox-series",
    "xbox series s": "xbox-series",
    "xbox series": "xbox-series",
    "series-x": "xbox-series",
    "xsx": "xbox-series",
    "xbox one": "xboxone",
    "xbox 360": "xbox360",
    # Nintendo
    "nintendo switch": "switch",
    "ns": "switch",
    "nintendo switch 2": "switch-2",
    "wii u": "wiiu",
    "nintendo 64": "n64",
    "nintendo gamecube": "gamecube",
    "ngc": "gamecube",
    "nintendo entertainment system": "nes",
    "famicom": "nes",
    "fc": "nes",
    "super nintendo entertainment system": "snes",
    "super famicom": "snes",
    "sfc": "snes",
    "game boy": "gb",
    "game boy color": "gbc",
    "game boy advance": "gba",
    "nintendo ds": "nds",
    "nintendo 3ds": "3ds",
    "new nintendo 3ds": "3ds",
    # Sega
    "sega dreamcast": "dreamcast",
    "sega saturn": "saturn",
    "sega mega drive/genesis": "genesis",
    "sega mega drive": "genesis",
    "mega drive": "genesis",
    "sega genesis": "genesis",
    "md": "genesis",
    # other
    "街机": "arcade",
    "board game": "boardgame",
    "tabletop game": "boardgame",
    "tabletop": "boardgame",
    "桌游": "boardgame",
    "桌面游戏": "boardgame",
}

_RE_SPLIT = re.compile(r"\s*[/,;，、；]\s*")


def normalize_game_platform(value: str) -> str | None:
    """Normalize a single platform value to a canonical slug; unknown
    values pass through as-is."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    vl = v.lower()
    if vl in GAME_PLATFORM_CODES:
        return vl
    if vl in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[vl]
    return v


def normalize_game_platforms(values: list[str] | str | None) -> list[str]:
    """Normalize platform value(s) to canonical slugs, deduplicated,
    order kept; unknown values pass through."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    result: list[str] = []
    for value in values:
        if not value or not isinstance(value, str):
            continue
        v = value.strip()
        vl = v.lower()
        if vl in GAME_PLATFORM_CODES or vl in _PLATFORM_ALIASES:
            slug = normalize_game_platform(v)
            if slug:
                result.append(slug)
            continue
        # split compound values only when the whole string does not match
        parts = [p for p in _RE_SPLIT.split(v) if p]
        for part in parts if len(parts) > 1 else [v]:
            slug = normalize_game_platform(part)
            if slug:
                result.append(slug)
    return list(dict.fromkeys(result))
