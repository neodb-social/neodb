import re
from html import unescape
from typing import cast

import mistune
import nh3
from django.conf import settings
from django.utils.html import escape
from django.utils.translation import gettext as _

from catalog.models import Item, ItemCategory

_mistune_plugins = [
    "url",
    "strikethrough",
    "footnotes",
    "table",
    "mark",
    "superscript",
    "subscript",
    "math",
    "spoiler",
    "ruby",
]
_markdown = mistune.create_markdown(plugins=_mistune_plugins)


def convert_leading_space_in_md(body: str) -> str:
    body = re.sub(r"^\s+$", "", body, flags=re.MULTILINE)
    body = re.sub(
        r"^(\u2003*)( +)",
        lambda s: "\u2003" * ((len(s[2]) + 1) // 2 + len(s[1])),
        body,
        flags=re.MULTILINE,
    )
    return body


def render_md(s: str) -> str:
    return cast(str, _markdown(s))


_RE_HTML_TAG = re.compile(r"<[^>]*>")

_URL_REGEX = re.compile(
    r"""(\b(?<![@.])(?:https?://(?:(?:\w+:)?\w+@)?)  # http://
    (?:[\w-]+\.)+(?:[\w-]+)(?:\:[0-9]+)?(?!\.\w)\b   # xx.yy.tld(:##)?
    (?:[/?][^\s\{{\}}\|\\\^\[\]`<>"]*)?)
    # /path/zz (excluding "unsafe" chars from RFC 1738,
    # except for # and ~, which happen in practice)
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)


def html_to_text(h: str) -> str:
    return unescape(
        _RE_HTML_TAG.sub(
            " ", h.replace("\r", "").replace("<br", "\n<br").replace("</p", "\n</p")
        )
    )


def _linkify(s: str) -> str:
    """Escape text and convert URLs to hyperlinks."""
    bits = _URL_REGEX.split(s)
    parts: list[str] = []
    for i, bit in enumerate(bits):
        if i % 2 == 1:
            escaped = escape(bit)
            parts.append(
                f'<a href="{escaped}" rel="nofollow" target="_blank">{escaped}</a>'
            )
        else:
            parts.append(escape(bit))
    return "".join(parts)


def has_spoiler(s: str) -> bool:
    return ">!" in s


def _spoiler(s: str) -> str:
    sl = s.split(">!", 1)
    if len(sl) == 1:
        return _linkify(s)
    r = sl[1].split("!<", 1)
    return (
        _linkify(sl[0])
        + '<span class="spoiler" _="on click toggle .revealed on me">'
        + _linkify(r[0])
        + "</span>"
        + (_spoiler(r[1]) if len(r) == 2 else "")
    )


def render_text(s: str) -> str:
    return _spoiler(s).strip().replace("\n", "<br>")


def render_title_as_hashtag(t: str) -> str:
    t = re.sub(r"[^\w]", "_", t)
    t = re.sub(r"__+", "_", t)
    t = re.sub(r"^(_*\d)", r"t_\1", t)
    return "#" + t


def render_post_with_macro(txt: str, item: Item) -> str:
    if not txt:
        return ""
    return (
        txt.replace("[category]", str(ItemCategory(item.category).label))
        .replace("#[title]", render_title_as_hashtag(item.display_title))
        .replace("[title]", item.display_title)
        .replace("[url]", item.absolute_url)
    )


def render_rating(score: int | None, star_mode=0) -> str:
    """convert score(0~10) to mastodon star emoji code"""
    if score is None or score == "" or score == 0:
        return ""
    solid_stars = score // 2
    half_star = int(bool(score % 2))
    empty_stars = 5 - solid_stars if not half_star else 5 - solid_stars - 1
    if star_mode == 0:
        emoji_code = "🌕" * solid_stars + "🌗" * half_star + "🌑" * empty_stars
    else:
        emoji_code = (
            settings.STAR_SOLID * solid_stars
            + settings.STAR_HALF * half_star
            + settings.STAR_EMPTY * empty_stars
        )
    emoji_code = emoji_code.replace("::", ": :")
    emoji_code = " " + emoji_code + " "
    return emoji_code


def render_spoiler_text(text, item):
    if text and text.find(">!") != -1:
        spoiler_text = _(
            "regarding {item_title}, may contain spoiler or triggering content"
        ).format(item_title=item.display_title)
        return spoiler_text, text.replace(">!", "").replace("!<", "")
    else:
        return None, text or ""


_post_allowed_tags = set(["a", "p", "span", "br", "div", "img"])


def bleach_post_content(text):
    return nh3.clean(text, tags=_post_allowed_tags)
