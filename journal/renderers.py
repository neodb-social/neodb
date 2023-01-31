from typing import cast
import mistune
import re
from django.utils.html import escape

MARKDOWNX_MARKDOWNIFY_FUNCTION = "journal.renderers.render_md"

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


def render_md(s) -> str:
    # s = "\n".join(
    #     [
    #         re.sub(r"^(\u2003+)", lambda s: "&emsp;" * len(s[0]), line)
    #         for line in s.split("\n")
    #     ]
    # )
    return cast(str, _markdown(s))


def _spolier(s):
    l = s.split(">!", 1)
    if len(l) == 1:
        return escape(s)
    r = l[1].split("!<", 1)
    return (
        escape(l[0])
        + '<span class="spoiler">'
        + escape(r[0])
        + "</span>"
        + (_spolier(r[1]) if len(r) == 2 else "")
    )


def render_text(s):
    return _spolier(s)
