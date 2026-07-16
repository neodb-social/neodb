"""
Album type and media format support

Canonical vocabularies for Album.album_type (nature of the release,
after MusicBrainz release-group types) and Album.media_format (physical
or digital medium, after MusicBrainz release formats, with rare formats
folded into their parent family). Unknown values pass through as custom
strings.

Pattern follows common/models/genre.py.
"""

import re

from django.utils.translation import pgettext_lazy

ALBUM_TYPE_CATALOG = {
    "album": pgettext_lazy("album_type", "Album"),
    "single": pgettext_lazy("album_type", "Single"),
    "ep": pgettext_lazy("album_type", "EP"),
    "compilation": pgettext_lazy("album_type", "Compilation"),
    "soundtrack": pgettext_lazy("album_type", "Soundtrack"),
    "live": pgettext_lazy("album_type", "Live"),
    "remix": pgettext_lazy("album_type", "Remix"),
    "dj-mix": pgettext_lazy("album_type", "DJ-Mix"),
    "mixtape": pgettext_lazy("album_type", "Mixtape"),
    "demo": pgettext_lazy("album_type", "Demo"),
    "spokenword": pgettext_lazy("album_type", "Spoken Word"),
    "audiobook": pgettext_lazy("album_type", "Audiobook"),
    "audio-drama": pgettext_lazy("album_type", "Audio Drama"),
    "interview": pgettext_lazy("album_type", "Interview"),
    "broadcast": pgettext_lazy("album_type", "Broadcast"),
    "field-recording": pgettext_lazy("album_type", "Field Recording"),
}

MEDIA_FORMAT_CATALOG = {
    "cd": pgettext_lazy("media_format", "CD"),
    "vinyl": pgettext_lazy("media_format", "Vinyl"),
    "cassette": pgettext_lazy("media_format", "Cassette"),
    "digital": pgettext_lazy("media_format", "Digital"),
    "sacd": pgettext_lazy("media_format", "SACD"),
    "dvd": pgettext_lazy("media_format", "DVD"),
    "blu-ray": pgettext_lazy("media_format", "Blu-ray"),
    "minidisc": pgettext_lazy("media_format", "MiniDisc"),
    "vcd": pgettext_lazy("media_format", "VCD"),
}

ALBUM_TYPE_CHOICES = list(ALBUM_TYPE_CATALOG.items())
ALBUM_TYPE_CODES = dict(ALBUM_TYPE_CATALOG)
MEDIA_FORMAT_CHOICES = list(MEDIA_FORMAT_CATALOG.items())
MEDIA_FORMAT_CODES = dict(MEDIA_FORMAT_CATALOG)

# Scraped value -> slug. Keys must be lowercase. Sources: Douban music
# (专辑类型/介质), Spotify album_type, Discogs formats, MusicBrainz
# release-group types and release formats.
_ALBUM_TYPE_ALIASES: dict[str, str] = {
    # Douban 专辑类型
    "专辑": "album",
    "录音室专辑": "album",
    "单曲": "single",
    "迷你专辑": "ep",
    "精选": "compilation",
    "精选集": "compilation",
    "合辑": "compilation",
    "原声": "soundtrack",
    "原声带": "soundtrack",
    "电影原声": "soundtrack",
    "现场": "live",
    "现场专辑": "live",
    "演唱会": "live",
    "混音": "remix",
    "有声书": "audiobook",
    "访谈": "interview",
    # Discogs format descriptions / common English variants
    "lp": "album",
    "mini-album": "ep",
    "mini album": "ep",
    "maxi-single": "single",
    "maxi single": "single",
    "ost": "soundtrack",
    # MusicBrainz secondary types not matching slugs directly
    "dj mix": "dj-mix",
    "mixtape/street": "mixtape",
    "street": "mixtape",
    "spoken word": "spokenword",
    "audio drama": "audio-drama",
    "field recording": "field-recording",
}

_MEDIA_FORMAT_ALIASES: dict[str, str] = {
    # CD family
    "audio cd": "cd",
    "compact disc": "cd",
    "cd-r": "cd",
    "hdcd": "cd",
    "shm-cd": "cd",
    "blu-spec cd": "cd",
    "blu-spec cd2": "cd",
    "hqcd": "cd",
    "enhanced cd": "cd",
    "copy control cd": "cd",
    "8cm cd": "cd",
    "cd+g": "cd",
    # Vinyl family
    "lp": "vinyl",
    "黑胶": "vinyl",
    "黑胶唱片": "vinyl",
    '7" vinyl': "vinyl",
    '10" vinyl': "vinyl",
    '12" vinyl': "vinyl",
    "shellac": "vinyl",
    "acetate": "vinyl",
    "flexi-disc": "vinyl",
    "vinyldisc": "vinyl",
    # Cassette / tape family
    "磁带": "cassette",
    "卡带": "cassette",
    "盒带": "cassette",
    "microcassette": "cassette",
    "dat": "cassette",
    "dcc": "cassette",
    "reel-to-reel": "cassette",
    "8-track cartridge": "cassette",
    "cartridge": "cassette",
    "playtape": "cassette",
    # Digital family
    "数字": "digital",
    "数字专辑": "digital",
    "数位": "digital",
    "digital media": "digital",
    "file": "digital",
    "download card": "digital",
    "usb flash drive": "digital",
    "sd card": "digital",
    "slotmusic": "digital",
    "流媒体": "digital",
    "streaming": "digital",
    # SACD family
    "super audio cd": "sacd",
    "hybrid sacd": "sacd",
    "shm-sacd": "sacd",
    "sacd (hybrid)": "sacd",
    # DVD family
    "dvd-audio": "dvd",
    "dvd audio": "dvd",
    "dvd-video": "dvd",
    "dualdisc": "dvd",
    "dvdplus": "dvd",
    # Blu-ray family
    "blu-ray-r": "blu-ray",
    "blu-ray audio": "blu-ray",
    "bluray": "blu-ray",
    "bd": "blu-ray",
    "hd-dvd": "blu-ray",
    "蓝光": "blu-ray",
    # MiniDisc
    "md": "minidisc",
    "mini disc": "minidisc",
    # Video media folded into vcd
    "svcd": "vcd",
    "cdv": "vcd",
    "laserdisc": "vcd",
    "ld": "vcd",
    "vhs": "vcd",
    "betamax": "vcd",
    "umd": "vcd",
    "vhd": "vcd",
    "ced": "vcd",
    "录像带": "vcd",
}

_RE_SPLIT = re.compile(r"\s*[/,;，、；]\s*")


def _normalize_values(
    values: list[str] | str | None,
    codes: dict,
    aliases: dict[str, str],
) -> list[str]:
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
        if vl in codes:
            result.append(vl)
        elif vl in aliases:
            result.append(aliases[vl])
        else:
            # split compound values ("Album, EP") only when the whole
            # string does not match, then map each part
            parts = [p for p in _RE_SPLIT.split(v) if p]
            if len(parts) > 1:
                result.extend(_normalize_values(parts, codes, aliases))
            else:
                result.append(v)  # pass through as custom value
    return list(dict.fromkeys(result))


def normalize_album_types(values: list[str] | str | None) -> list[str]:
    """Normalize album type value(s) to canonical slugs; unknown values
    pass through."""
    return _normalize_values(values, ALBUM_TYPE_CODES, _ALBUM_TYPE_ALIASES)


def normalize_media_formats(values: list[str] | str | None) -> list[str]:
    """Normalize media format value(s) to canonical slugs; unknown
    values pass through."""
    return _normalize_values(values, MEDIA_FORMAT_CODES, _MEDIA_FORMAT_ALIASES)
