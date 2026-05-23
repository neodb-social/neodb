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
    assert parser.html == "<p>List:</p><p>One<br>Two<br>Three</p><p>End!</p>"
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
