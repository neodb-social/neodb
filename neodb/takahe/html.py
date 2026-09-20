import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from django.utils.safestring import mark_safe

_ALLOWED_SCHEMES = {"http", "https", "mailto"}


class FediverseHtmlParser(HTMLParser):
    """
    A custom HTML parser that only allows a certain tag subset and behaviour:
    - block and inline formatting tags on the passthrough lists are kept, with
      every attribute dropped, and the output is balanced
    - a tags are passed through if they're not hashtags or mentions
    - headings are converted to p > strong
    - any other tag is dropped, keeping its text

    It also linkifies URLs, mentions, hashtags, and imagifies emoji.
    """

    # Block-level tags emitted as themselves. Remote instances send lists,
    # quotes and code blocks; rewriting them all to <p> loses the structure.
    PASSTHROUGH_BLOCKS = [
        "p",
        "blockquote",
        "pre",
        "ul",
        "ol",
        "li",
    ]

    # Inline formatting emitted as itself. This is Mastodon's allow list less
    # span, which stays dropped so that <span>#</span>tag still reads as a
    # single hashtag.
    PASSTHROUGH_INLINE = [
        "code",
        "b",
        "strong",
        "i",
        "em",
        "u",
        "del",
        "s",
        "ruby",
        "rt",
        "rp",
    ]

    # A heading would dominate a post card, so demote it as Mastodon does.
    REWRITE_TO_STRONG_P = [
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    ]

    REWRITE_TO_BR = [
        "br",
    ]

    # Closing one of these ends a paragraph in the plain text rendering.
    TEXT_BLOCK_TAGS = {"p", "blockquote", "pre", "ul", "ol", *REWRITE_TO_STRONG_P}

    # Content of these is literal: no linkifying, hashtags or emoji.
    LITERAL_TAGS = {"pre", "code"}

    # Every open tag is a level of nesting in the output, and HTMLParser does
    # not build a tree, so cap it before a hostile post nests without bound.
    MAX_NESTING = 32

    MENTION_REGEX = re.compile(
        r"(^|[^\w\d\-_/])@([\w\d\-_.]+(?:@[\w\d\-_\.]+[\w\d\-_]+)?)"
    )

    HASHTAG_REGEX = re.compile(r"\B#([\w()]+\b)(?!;)")

    EMOJI_REGEX = re.compile(r"\B:([a-zA-Z0-9(_)-]+):\B")

    IMG_EMOJI_REGEX = re.compile(r"^:([a-zA-Z0-9(_)-]+):$")

    URL_REGEX = re.compile(
        r"""(\(*  # Match any opening parentheses.
        \b(?<![@.])(?:https?://(?:(?:\w+:)?\w+@)?)  # http://
        (?:[\w-]+\.)+(?:[\w-]+)(?:\:[0-9]+)?(?!\.\w)\b   # xx.yy.tld(:##)?
        (?:[/?][^\s\{{\}}\|\\\^\[\]`<>"]*)?)
        # /path/zz (excluding "unsafe" chars from RFC 1738,
        # except for # and ~, which happen in practice)
        """,
        re.IGNORECASE | re.VERBOSE | re.UNICODE,
    )

    def __init__(
        self,
        html: str,
        uri_domain: str | None = None,
        mentions: list | None = None,
        find_mentions: bool = False,
        find_hashtags: bool = False,
        find_emojis: bool = False,
        emoji_domain=None,
    ):
        super().__init__()
        self.uri_domain = uri_domain
        self.emoji_domain = emoji_domain
        self.find_mentions = find_mentions
        self.find_hashtags = find_hashtags
        self.find_emojis = find_emojis
        self.calculate_mentions(mentions)
        self._data_buffer = ""
        self.html_output = ""
        self.text_output = ""
        self.emojis: set[str] = set()
        self.mentions: set[str] = set()
        self.hashtags: set[str] = set()
        self._pending_a: dict | None = None
        self._fresh_p = False
        self._open_tags: list[tuple[str, str]] = []
        self.feed(html)
        self.flush_data()
        self.close_all()

    def calculate_mentions(self, mentions: list | None):
        """
        Prepares a set of content that we expect to see mentions look like
        (this imp)
        """
        self.mention_matches: dict[str, str] = {}
        self.mention_aliases: dict[str, str] = {}
        # Sort for deterministic resolution: the bare (un-domained) username key
        # is shared by identities that have the same username on different
        # domains, and the last writer wins. `mentions` is otherwise unordered.
        for mention in sorted(
            mentions or [],
            key=lambda m: ((m.username or "").lower(), (m.domain_id or "").lower()),
        ):
            if self.uri_domain:
                url = mention.absolute_profile_uri()
            elif not mention.local:
                url = mention.profile_uri
            else:
                url = str(mention.urls.view)
            # profile_uri is nullable, and absolute_profile_uri() hands it back
            # verbatim for a remote identity, so either branch can yield None.
            # Fall back to the local profile page instead of losing the link.
            url = url or str(mention.urls.view)
            if mention.username:
                username = mention.username.lower()
                domain = mention.domain_id.lower()
                self.mention_matches[f"{username}"] = url
                self.mention_matches[f"{username}@{domain}"] = url
                self.mention_matches[mention.absolute_profile_uri()] = url

    @property
    def in_literal(self) -> bool:
        """Whether the parser is inside a tag whose content is verbatim."""
        return any(tag in self.LITERAL_TAGS for tag, _ in self._open_tags)

    def push_tag(self, tag: str, opening: str, closing: str) -> bool:
        """Emit an opening tag and remember how to close it.

        False when the nesting cap dropped it, so the caller can skip whatever
        else it would have recorded for a tag that is not in the output.
        """
        if len(self._open_tags) >= self.MAX_NESTING:
            return False
        self.html_output += opening
        self._open_tags.append((tag, closing))
        return True

    def close_innermost(self) -> None:
        self.html_output += self._open_tags.pop()[1]

    def close_tag(self, tag: str) -> bool:
        """Close up to and including the innermost open `tag`.

        False when no such tag was open, either because the closing tag was
        stray or because the nesting cap dropped the opening one.
        """
        for index in range(len(self._open_tags) - 1, -1, -1):
            if self._open_tags[index][0] == tag:
                while len(self._open_tags) > index:
                    self.close_innermost()
                return True
        return False

    def close_all(self) -> None:
        """Balance the output. Remote HTML leaves tags open more often than not."""
        while self._open_tags:
            self.close_innermost()

    def open_block(self, tag: str) -> None:
        # A <p> holds no block, and an <li> holds no <li>. Remote HTML leans on
        # the browser to close both, so close them here instead.
        while self._open_tags and self._open_tags[-1][0] == "p":
            self.close_innermost()
        if tag == "li":
            while self._open_tags and self._open_tags[-1][0] == "li":
                self.close_innermost()
        if tag in self.REWRITE_TO_STRONG_P:
            pushed = self.push_tag(tag, "<p><strong>", "</strong></p>")
        else:
            pushed = self.push_tag(tag, f"<{tag}>", f"</{tag}>")
        # Only after the push, or a list item dropped by the nesting cap still
        # starts a line in the plain text rendering.
        if pushed and tag == "li" and not self._fresh_p:
            self.text_output += "\n"

    def handle_emoji_img(self, attrs: dict[str, str | None]) -> None:
        alt = attrs.get("alt") or ""
        m = self.IMG_EMOJI_REGEX.match(alt.strip())
        if not m:
            return
        shortcode = m.group(1)
        if self._pending_a:
            self._pending_a["content"] += f":{shortcode}:"
            return
        self.flush_data()
        if self.find_emojis:
            self.html_output += self.create_emoji(shortcode)
        else:
            self.html_output += html.escape(f":{shortcode}:")
        self.text_output += f":{shortcode}:"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        is_block = tag in self.PASSTHROUGH_BLOCKS or tag in self.REWRITE_TO_STRONG_P
        if tag == "img":
            self.handle_emoji_img(dict(attrs))
        elif self._pending_a is not None:
            # Keep link labels plain. Markup opened inside <a> would be emitted
            # ahead of the link that handle_endtag builds from the buffer.
            # Nothing was emitted, so _fresh_p has to keep its current value.
            return
        elif tag in self.REWRITE_TO_BR:
            self.flush_data()
            if not self._fresh_p:
                self.html_output += "<br>"
                self.text_output += "\n"
        elif tag == "a":
            self.flush_data()
            self._pending_a = {"attrs": dict(attrs), "content": ""}
        elif is_block:
            self.flush_data()
            self.open_block(tag)
        elif tag in self.PASSTHROUGH_INLINE:
            self.flush_data()
            self.push_tag(tag, f"<{tag}>", f"</{tag}>")
        self._fresh_p = is_block

    def handle_endtag(self, tag: str) -> None:
        self._fresh_p = False
        if tag != "a" and self._pending_a is not None:
            pass
        elif (
            tag in self.PASSTHROUGH_BLOCKS
            or tag in self.PASSTHROUGH_INLINE
            or tag in self.REWRITE_TO_STRONG_P
        ):
            self.flush_data()
            # Only break the paragraph for a block that is really in the
            # output, or the plain text gains blank lines the HTML has not.
            if self.close_tag(tag) and tag in self.TEXT_BLOCK_TAGS:
                self.text_output += "\n\n"
        elif tag == "a":
            if self._pending_a:
                href = self._pending_a["attrs"].get("href", "#")
                content = self._pending_a["content"].strip()
                has_ellipsis = "ellipsis" in self._pending_a["attrs"].get("class", "")
                # Is it a mention?
                if content.lower().lstrip("@") in self.mention_matches:
                    self.html_output += self.create_mention(content, href)
                    self.text_output += content
                # Is it a hashtag?
                elif self.HASHTAG_REGEX.match(content):
                    self.html_output += self.create_hashtag(content)
                    self.text_output += content
                elif content:
                    # Shorten the link if we need to
                    self.html_output += self.create_link(
                        href,
                        content,
                        has_ellipsis=has_ellipsis,
                    )
                    self.text_output += href
                self._pending_a = None

    def handle_data(self, data: str) -> None:
        if not self.in_literal:
            # Newlines are insignificant outside <pre>, and dropping them here
            # rather than before feed() is what lets <pre> keep its own.
            data = data.replace("\n", "")
            if not data:
                return
        self._fresh_p = False
        if self._pending_a:
            self._pending_a["content"] += data
        else:
            self._data_buffer += data

    def flush_data(self) -> None:
        """
        We collect data segments until we encounter a tag we care about,
        so we can treat <span>#</span>hashtag as #hashtag
        """
        self.text_output += self._data_buffer
        if self.in_literal:
            self.html_output += html.escape(self._data_buffer)
        else:
            self.html_output += self.linkify(self._data_buffer)
        self._data_buffer = ""

    def create_link(self, href, content, has_ellipsis=False):
        """
        Generates a link, doing optional shortening.

        All return values from this function should be HTML-safe.
        """
        scheme = urlparse(href).scheme.lower()
        if scheme and scheme not in _ALLOWED_SCHEMES:
            return html.escape(content)
        looks_like_link = bool(self.URL_REGEX.match(content))
        if looks_like_link:
            protocol, content = content.split("://", 1)
        else:
            protocol = ""
        if (looks_like_link and len(content) > 30) or has_ellipsis:
            return f'<a href="{html.escape(href)}" rel="nofollow" class="ellipsis" title="{html.escape(content)}"><span class="invisible">{html.escape(protocol)}://</span><span class="ellipsis">{html.escape(content[:30])}</span><span class="invisible">{html.escape(content[30:])}</span></a>'
        elif looks_like_link:
            return f'<a href="{html.escape(href)}" rel="nofollow"><span class="invisible">{html.escape(protocol)}://</span>{html.escape(content)}</a>'
        else:
            return f'<a href="{html.escape(href)}" rel="nofollow">{html.escape(content)}</a>'

    def create_mention(self, handle, href: str | None = None) -> str:
        """
        Generates a mention link. Handle should have a leading @.

        All return values from this function should be HTML-safe
        """
        handle = handle.lstrip("@")
        if "@" in handle:
            short_handle = handle.split("@", 1)[0]
        else:
            short_handle = handle
        handle_hash = handle.lower()
        short_hash = short_handle.lower()
        self.mentions.add(handle_hash)
        url = self.mention_matches.get(handle_hash)
        # If we have a captured link out, use that as the actual resolver
        if href and href in self.mention_matches:
            url = self.mention_matches[href]
        if url:
            if short_hash not in self.mention_aliases:
                self.mention_aliases[short_hash] = handle_hash
            elif self.mention_aliases.get(short_hash) != handle_hash:
                short_handle = handle
            return f'<span class="h-card"><a href="{html.escape(url)}" class="u-url mention" rel="nofollow noopener noreferrer" target="_blank">@<span>{html.escape(short_handle)}</span></a></span>'
        else:
            return "@" + html.escape(handle)

    def create_hashtag(self, hashtag) -> str:
        """
        Generates a hashtag link. Hashtag does not need to start with #

        All return values from this function should be HTML-safe
        """
        hashtag = hashtag.lstrip("#")
        self.hashtags.add(hashtag.lower())
        if self.uri_domain:
            return f'<a href="https://{self.uri_domain}/tags/{hashtag.lower()}/" class="mention hashtag" rel="tag">#{hashtag}</a>'
        else:
            return f'<a href="/tags/{hashtag.lower()}/" rel="tag">#{hashtag}</a>'

    def create_emoji(self, shortcode) -> str:
        """
        Generates an emoji <img> tag

        All return values from this function should be HTML-safe
        """
        from .models import Emoji

        emoji = Emoji.get_by_domain(shortcode, self.emoji_domain)
        if emoji and emoji.is_usable:
            self.emojis.add(shortcode)
            return emoji.as_html()
        return f":{shortcode}:"

    def linkify(self, data):
        """
        Linkifies some content that is plaintext.

        Handles URLs first, then mentions. Note that this takes great care to
        keep track of what is HTML and what needs to be escaped.
        """
        # Split the string by the URL regex so we know what to escape and what
        # not to escape.
        bits = self.URL_REGEX.split(data)
        result = ""
        # Even indices are data we should pass though, odd indices are links
        for i, bit in enumerate(bits):
            # A link!
            if i % 2 == 1:
                result += self.create_link(bit, bit)
            # Not a link
            elif self.mention_matches or self.find_mentions:
                result += self.linkify_mentions(bit)
            elif self.find_hashtags:
                result += self.linkify_hashtags(bit)
            elif self.find_emojis:
                result += self.linkify_emoji(bit)
            else:
                result += html.escape(bit)
        return result

    def linkify_mentions(self, data):
        """
        Linkifies mentions
        """
        bits = self.MENTION_REGEX.split(data)
        result = ""
        for i, bit in enumerate(bits):
            # Mention content
            if i % 3 == 2:
                result += self.create_mention(bit)
            # Not part of a mention (0) or mention preamble (1)
            elif self.find_hashtags:
                result += self.linkify_hashtags(bit)
            elif self.find_emojis:
                result += self.linkify_emoji(bit)
            else:
                result += html.escape(bit)
        return result

    def linkify_hashtags(self, data):
        """
        Linkifies hashtags
        """
        bits = self.HASHTAG_REGEX.split(data)
        result = ""
        for i, bit in enumerate(bits):
            # Not part of a hashtag
            if i % 2 == 0:
                if self.find_emojis:
                    result += self.linkify_emoji(bit)
                else:
                    result += html.escape(bit)
            # Hashtag content
            else:
                result += self.create_hashtag(bit)
        return result

    def linkify_emoji(self, data):
        """
        Linkifies emoji
        """
        bits = self.EMOJI_REGEX.split(data)
        result = ""
        for i, bit in enumerate(bits):
            # Not part of an emoji
            if i % 2 == 0:
                result += html.escape(bit)
            # Emoji content
            else:
                result += self.create_emoji(bit)
        return result

    @property
    def html(self):
        return self.html_output.strip()

    @property
    def plain_text(self):
        return self.text_output.strip()


class ContentRenderer:
    """
    Renders HTML for posts, identity fields, and more.

    The `local` parameter affects whether links are absolute (False) or relative (True)
    """

    def __init__(self, local: bool):
        self.local = local

    def render_post(self, html: str, post) -> str:
        """
        Given post HTML, normalises it and renders it for presentation.
        """
        if not html:
            return ""
        parser = FediverseHtmlParser(
            html,
            mentions=post.mentions.all(),
            uri_domain=(None if self.local else post.author.uri_domain),
            find_hashtags=True,
            find_emojis=self.local,
            emoji_domain=post.author.domain,
        )
        return mark_safe(parser.html)

    def render_identity_summary(self, html: str, identity) -> str:
        """
        Given identity summary HTML, normalises it and renders it for presentation.
        """
        if not html:
            return ""
        parser = FediverseHtmlParser(
            html,
            uri_domain=(None if self.local else identity.uri_domain),
            find_hashtags=True,
            find_emojis=self.local,
            emoji_domain=identity.domain,
        )
        return mark_safe(parser.html)

    def render_identity_data(self, html: str, identity, strip: bool = False) -> str:
        """
        Given name/basic value HTML, normalises it and renders it for presentation.
        """
        if not html:
            return ""
        parser = FediverseHtmlParser(
            html,
            uri_domain=(None if self.local else identity.uri_domain),
            find_hashtags=False,
            find_emojis=self.local,
            emoji_domain=identity.domain,
        )
        if strip:
            return mark_safe(parser.html)
        else:
            return mark_safe(parser.html)
