import re
from typing import TYPE_CHECKING, cast

import mistune
from django.utils.html import escape

if TYPE_CHECKING:
    from catalog.models import Item
    from users.models import User

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


def _spolier(s: str) -> str:
    sl = s.split(">!", 1)
    if len(sl) == 1:
        return escape(s)
    r = sl[1].split("!<", 1)
    return (
        escape(sl[0])
        + '<span class="spoiler" _="on click toggle .revealed on me">'
        + escape(r[0])
        + "</span>"
        + (_spolier(r[1]) if len(r) == 2 else "")
    )


def render_text(s: str) -> str:
    return _spolier(s).strip().replace("\n", "<br>")


def render_post_with_macro(txt: str, item: "Item") -> str:
    return (
        txt.replace("[category]", item.category.name)
        .replace("[title]", item.display_title)
        .replace("[url]", item.absolute_url)
    )
