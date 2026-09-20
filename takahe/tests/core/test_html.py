import pytest
from django.template.defaultfilters import linebreaks_filter

from core.html import FediverseHtmlParser


@pytest.mark.django_db
def test_parser(identity):
    """
    Validates the HtmlParser in its various output modes
    """

    # Basic tag allowance
    parser = FediverseHtmlParser("<p>Hello!</p><script></script>")
    assert parser.html == "<p>Hello!</p>"
    assert parser.plain_text == "Hello!"

    # Newline erasure
    parser = FediverseHtmlParser("<p>Hi!</p>\n\n<p>How are you?</p>")
    assert parser.html == "<p>Hi!</p><p>How are you?</p>"
    assert parser.plain_text == "Hi!\n\nHow are you?"

    # Trying to be evil
    parser = FediverseHtmlParser("<scri<span></span>pt>")
    assert "<scr" not in parser.html
    parser = FediverseHtmlParser("<scri #hashtag pt>")
    assert "<scr" not in parser.html

    # Entities are escaped
    parser = FediverseHtmlParser("<p>It&#39;s great</p>", find_hashtags=True)
    assert parser.html == "<p>It&#x27;s great</p>"
    assert parser.plain_text == "It's great"
    assert parser.hashtags == set()

    # Linkify works, but only with protocol prefixes
    parser = FediverseHtmlParser("<p>test.com</p>")
    assert parser.html == "<p>test.com</p>"
    assert parser.plain_text == "test.com"
    parser = FediverseHtmlParser("<p>https://test.com</p>")
    assert (
        parser.html
        == '<p><a href="https://test.com" rel="nofollow"><span class="invisible">https://</span>test.com</a></p>'
    )
    assert parser.plain_text == "https://test.com"

    # Links are preserved
    parser = FediverseHtmlParser("<a href='https://takahe.social'>takahe social</a>")
    assert (
        parser.html
        == '<a href="https://takahe.social" rel="nofollow">takahe social</a>'
    )
    assert parser.plain_text == "https://takahe.social"

    # Very long links are shortened
    full_url = "https://social.example.com/a-long/path/that-should-be-shortened"
    parser = FediverseHtmlParser(f"<p>{full_url}</p>")
    assert (
        parser.html
        == f'<p><a href="{full_url}" rel="nofollow" class="ellipsis" title="{full_url.removeprefix("https://")}"><span class="invisible">https://</span><span class="ellipsis">social.example.com/a-long/path</span><span class="invisible">/that-should-be-shortened</span></a></p>'
    )
    assert (
        parser.plain_text
        == "https://social.example.com/a-long/path/that-should-be-shortened"
    )

    # Make sure things that look like mentions are left alone with no mentions supplied.
    parser = FediverseHtmlParser(
        "<p>@test@example.com</p>",
        find_mentions=True,
        find_hashtags=True,
        find_emojis=True,
    )
    assert parser.html == "<p>@test@example.com</p>"
    assert parser.plain_text == "@test@example.com"
    assert parser.mentions == {"test@example.com"}

    # Make sure mentions work when there is a mention supplied
    parser = FediverseHtmlParser(
        "<p>@test@example.com</p>",
        mentions=[identity],
        find_hashtags=True,
        find_emojis=True,
    )
    assert (
        parser.html
        == '<p><span class="h-card"><a href="/@test@example.com/" class="u-url mention" rel="nofollow noopener noreferrer" target="_blank">@<span>test</span></a></span></p>'
    )
    assert parser.plain_text == "@test@example.com"
    assert parser.mentions == {"test@example.com"}

    # Ensure mentions are case insensitive
    parser = FediverseHtmlParser(
        "<p>@TeSt@ExamPle.com</p>",
        mentions=[identity],
        find_hashtags=True,
        find_emojis=True,
    )
    assert (
        parser.html
        == '<p><span class="h-card"><a href="/@test@example.com/" class="u-url mention" rel="nofollow noopener noreferrer" target="_blank">@<span>TeSt</span></a></span></p>'
    )
    assert parser.plain_text == "@TeSt@ExamPle.com"
    assert parser.mentions == {"test@example.com"}

    # Ensure hashtags are parsed and linkified in local posts
    parser = FediverseHtmlParser(
        linebreaks_filter("#tag1-x,#tag2 #标签。"), find_hashtags=True
    )
    assert (
        parser.html
        == '<p><a href="/tags/tag1/" rel="tag">#tag1</a>-x,<a href="/tags/tag2/" rel="tag">#tag2</a> <a href="/tags/标签/" rel="tag">#标签</a>。</p>'
    )
    assert parser.hashtags == {"tag1", "tag2", "标签"}

    # Ensure hashtags are linked, even through spans, but not within hrefs
    parser = FediverseHtmlParser(
        '<a href="http://example.com#notahashtag">something</a> <span>#</span>hashtag <a href="https://example.com/tags/hashtagtwo/">#hashtagtwo</a>',
        find_hashtags=True,
        find_emojis=True,
    )
    assert (
        parser.html
        == '<a href="http://example.com#notahashtag" rel="nofollow">something</a> <a href="/tags/hashtag/" rel="tag">#hashtag</a> <a href="/tags/hashtagtwo/" rel="tag">#hashtagtwo</a>'
    )
    assert parser.plain_text == "http://example.com#notahashtag #hashtag #hashtagtwo"
    assert parser.hashtags == {"hashtag", "hashtagtwo"}

    # Ensure lists are rendered reasonably
    parser = FediverseHtmlParser(
        "<p>List:</p><ul><li>One</li><li>Two</li><li>Three</li></ul><p>End!</p>",
        find_hashtags=True,
        find_emojis=True,
    )
    assert (
        parser.html
        == "<p>List:</p><ul><li>One</li><li>Two</li><li>Three</li></ul><p>End!</p>"
    )
    assert parser.plain_text == "List:\n\nOne\nTwo\nThree\n\nEnd!"


@pytest.mark.django_db
def test_parser_same_name_mentions(remote_identity, remote_identity2):
    """
    Ensure mentions that differ only by link are parsed right
    """

    parser = FediverseHtmlParser(
        '<span class="h-card"><a href="https://remote.test/@test/" class="u-url mention" rel="nofollow noreferrer noopener" target="_blank">@<span>test</span></a></span> <span class="h-card"><a href="https://remote2.test/@test/" class="u-url mention" rel="nofollow noreferrer noopener" target="_blank">@<span>test</span></a></span>',
        mentions=[remote_identity, remote_identity2],
        find_hashtags=True,
        find_emojis=True,
    )
    assert (
        parser.html
        == '<span class="h-card"><a href="https://remote.test/@test/" class="u-url mention" rel="nofollow noopener noreferrer" target="_blank">@<span>test</span></a></span> <span class="h-card"><a href="https://remote2.test/@test/" class="u-url mention" rel="nofollow noopener noreferrer" target="_blank">@<span>test</span></a></span>'
    )
    assert parser.plain_text == "@test @test"


@pytest.mark.django_db
def test_parser_bare_mention_deterministic(remote_identity, remote_identity2):
    """
    A bare @username shared by identities on different domains must resolve
    deterministically, regardless of the order mentions are supplied in (#1616).
    """

    def render(mentions):
        return FediverseHtmlParser("<p>Hey @test</p>", mentions=mentions).html

    forward = render([remote_identity, remote_identity2])
    backward = render([remote_identity2, remote_identity])
    assert forward == backward
    assert 'href="https://remote2.test/@test/"' in forward
    assert 'href="https://remote.test/@test/"' not in forward


@pytest.mark.django_db
def test_parser_emoji_img():
    """
    Validates that <img> tags with :shortcode: alt text are handled as emoji
    """

    # Emoji img with find_emojis=False: shortcode text preserved
    parser = FediverseHtmlParser(
        '<p>Hello <img src="https://remote.test/emoji/blobcat.png" alt=":blobcat:" class="custom-emoji"> world</p>',
        find_emojis=False,
    )
    assert parser.html == "<p>Hello :blobcat: world</p>"
    assert parser.plain_text == "Hello :blobcat: world"

    # Emoji img with find_emojis=True but no DB emoji: falls back to text
    parser = FediverseHtmlParser(
        '<p>Hello <img src="https://remote.test/emoji/blobcat.png" alt=":blobcat:" class="custom-emoji"> world</p>',
        find_emojis=True,
    )
    assert parser.html == "<p>Hello :blobcat: world</p>"
    assert parser.plain_text == "Hello :blobcat: world"

    # Non-emoji img (alt doesn't match :shortcode:): tag silently dropped
    parser = FediverseHtmlParser(
        '<p>Hello <img src="https://remote.test/photo.jpg" alt="A photo"> world</p>',
    )
    assert parser.html == "<p>Hello  world</p>"
    assert parser.plain_text == "Hello  world"

    # Img with no alt attribute: tag silently dropped
    parser = FediverseHtmlParser(
        '<p>Hello <img src="https://remote.test/photo.jpg"> world</p>',
    )
    assert parser.html == "<p>Hello  world</p>"
    assert parser.plain_text == "Hello  world"

    # Multiple emoji img tags
    parser = FediverseHtmlParser(
        '<p><img src="https://remote.test/emoji/a.png" alt=":wave:"> hi <img src="https://remote.test/emoji/b.png" alt=":smile:"></p>',
        find_emojis=False,
    )
    assert parser.html == "<p>:wave: hi :smile:</p>"
    assert parser.plain_text == ":wave: hi :smile:"

    # Self-closing img tag
    parser = FediverseHtmlParser(
        '<p>Test <img src="https://remote.test/emoji/a.png" alt=":cat:" /> end</p>',
        find_emojis=False,
    )
    assert parser.html == "<p>Test :cat: end</p>"
    assert parser.plain_text == "Test :cat: end"


@pytest.mark.django_db
def test_parser_link_scheme_validation():
    """
    Validates that links with disallowed schemes are stripped
    """

    # javascript: scheme is stripped, content preserved as text
    parser = FediverseHtmlParser(
        '<a href="javascript:alert(1)">click me</a>',
    )
    assert parser.html == "click me"
    assert "javascript" not in parser.html

    # data: scheme is stripped
    parser = FediverseHtmlParser(
        '<a href="data:text/html,&lt;script&gt;">payload</a>',
    )
    assert "href" not in parser.html

    # vbscript: scheme is stripped
    parser = FediverseHtmlParser(
        '<a href="vbscript:MsgBox(1)">click</a>',
    )
    assert parser.html == "click"
    assert "href" not in parser.html

    # http: and https: are allowed
    parser = FediverseHtmlParser(
        '<a href="https://example.com">safe link</a>',
    )
    assert 'href="https://example.com"' in parser.html

    parser = FediverseHtmlParser(
        '<a href="http://example.com">http link</a>',
    )
    assert 'href="http://example.com"' in parser.html

    # mailto: is allowed
    parser = FediverseHtmlParser(
        '<a href="mailto:user@example.com">email</a>',
    )
    assert 'href="mailto:user@example.com"' in parser.html

    # Relative URLs (no scheme) are allowed
    parser = FediverseHtmlParser(
        '<a href="/local/path">local</a>',
    )
    assert 'href="/local/path"' in parser.html


def test_parser_keeps_rich_structure():
    """
    Lists, quotes, code blocks and inline formatting survive from remote posts
    """

    parser = FediverseHtmlParser("<ol><li>First</li><li>Second</li></ol>")
    assert parser.html == "<ol><li>First</li><li>Second</li></ol>"
    assert parser.plain_text == "First\nSecond"

    parser = FediverseHtmlParser(
        "<p>They said:</p><blockquote><p>a quote</p></blockquote><p>end</p>"
    )
    assert (
        parser.html
        == "<p>They said:</p><blockquote><p>a quote</p></blockquote><p>end</p>"
    )

    parser = FediverseHtmlParser(
        "<p>This is <strong>bold</strong>, <em>italic</em>, <code>code</code>"
        " and <del>struck</del></p>"
    )
    assert parser.html == (
        "<p>This is <strong>bold</strong>, <em>italic</em>, <code>code</code>"
        " and <del>struck</del></p>"
    )
    assert parser.plain_text == "This is bold, italic, code and struck"

    # Headings are demoted rather than passed through
    parser = FediverseHtmlParser("<h2>Title</h2><p>body</p>")
    assert parser.html == "<p><strong>Title</strong></p><p>body</p>"
    assert parser.plain_text == "Title\n\nbody"

    # Tags outside the allow list are still dropped, keeping their text
    parser = FediverseHtmlParser("<table><tr><td>cell</td></tr></table>")
    assert parser.html == "cell"
    parser = FediverseHtmlParser("<p>H<sub>2</sub>O</p>")
    assert parser.html == "<p>H2O</p>"

    # Attributes are dropped from everything we pass through
    parser = FediverseHtmlParser(
        '<ul class="x" onclick="evil()"><li style="color:red">y</li></ul>'
    )
    assert parser.html == "<ul><li>y</li></ul>"


def test_parser_preserves_pre_newlines():
    """
    Newlines are insignificant everywhere but <pre>, which keeps its own
    """

    parser = FediverseHtmlParser("<pre><code>def f():\n    pass</code></pre>")
    assert parser.html == "<pre><code>def f():\n    pass</code></pre>"
    assert parser.plain_text == "def f():\n    pass"

    parser = FediverseHtmlParser("<p>one\ntwo</p>")
    assert parser.html == "<p>onetwo</p>"


def test_parser_does_not_linkify_literal_content():
    """
    Code is quoted verbatim, so nothing in it becomes a link or a hashtag
    """

    parser = FediverseHtmlParser(
        "<pre><code>#include &lt;stdio.h&gt; https://example.com/x</code></pre>",
        find_hashtags=True,
    )
    assert parser.html == (
        "<pre><code>#include &lt;stdio.h&gt; https://example.com/x</code></pre>"
    )
    assert parser.hashtags == set()

    # ...but a hashtag outside the code block is still found
    parser = FediverseHtmlParser("<p>#real</p><code>#fake</code>", find_hashtags=True)
    assert parser.hashtags == {"real"}


def test_parser_balances_output():
    """
    Remote HTML leaves tags open. An unclosed <pre> must not eat the page.
    """

    assert FediverseHtmlParser("<p>a</p><pre>rest").html == "<p>a</p><pre>rest</pre>"
    assert FediverseHtmlParser("<ul><li>x").html == "<ul><li>x</li></ul>"
    assert FediverseHtmlParser("<strong>x").html == "<strong>x</strong>"

    # Implicit closes, the way a browser would read them
    assert FediverseHtmlParser("<ul><li>a<li>b</ul>").html == (
        "<ul><li>a</li><li>b</li></ul>"
    )
    assert FediverseHtmlParser("<p>a<p>b").html == "<p>a</p><p>b</p>"
    assert FediverseHtmlParser("<p>a<ul><li>b</ul>").html == (
        "<p>a</p><ul><li>b</li></ul>"
    )

    # Crossed tags close in the right order
    assert FediverseHtmlParser("<p><strong>bold</p>more").html == (
        "<p><strong>bold</strong></p>more"
    )

    # Unmatched closing tags are ignored
    assert FediverseHtmlParser("</p></ul><p>ok</p>").html == "<p>ok</p>"

    # Nesting is capped, and still balanced at the cap
    deep = "<blockquote>" * 60 + "x" + "</blockquote>" * 60
    html = FediverseHtmlParser(deep).html
    assert html.count("<blockquote>") == FediverseHtmlParser.MAX_NESTING
    assert html.count("</blockquote>") == FediverseHtmlParser.MAX_NESTING

    # An <li> the cap drops must not leave a line behind in the plain text
    over = "<ul>" * 40 + "<li>a</li>" + "</ul>" * 40
    parser = FediverseHtmlParser(over)
    assert "<li>" not in parser.html
    assert parser.plain_text == "a"


def test_parser_keeps_link_labels_plain():
    """
    Markup inside <a> is dropped; the link is rebuilt from its text
    """

    parser = FediverseHtmlParser(
        '<p><a href="https://example.com/"><strong>bold link</strong></a></p>'
    )
    assert parser.html == (
        '<p><a href="https://example.com/" rel="nofollow">bold link</a></p>'
    )
