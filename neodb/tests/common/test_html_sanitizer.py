import pytest

from takahe.html import FediverseHtmlParser


@pytest.mark.parametrize(
    "link_text",
    [
        "#t&lt;img src=x onerror=alert(1)&gt;",
        '#t"onmouseover="alert(1)',
    ],
)
def test_hashtag_link_text_is_escaped(link_text: str) -> None:
    parser = FediverseHtmlParser(
        f'<a href="https://example.com/">{link_text}</a>', find_hashtags=True
    )
    assert "<img" not in parser.html
    assert 'href="https://example.com/"' in parser.html
    assert '"onmouseover' not in parser.html
    assert parser.hashtags == set()


def test_hashtag_link_still_linkified() -> None:
    parser = FediverseHtmlParser(
        '<a href="https://example.com/tags/rust/">#rust</a>', find_hashtags=True
    )
    assert parser.html == '<a href="/tags/rust/" rel="tag">#rust</a>'
    assert parser.hashtags == {"rust"}


def test_create_hashtag_escapes() -> None:
    parser = FediverseHtmlParser("", uri_domain='a.example"><b')
    assert parser.create_hashtag('#a"<b>') == (
        '<a href="https://a.example&quot;&gt;&lt;b/tags/a&quot;&lt;b&gt;/"'
        ' class="mention hashtag" rel="tag">#a&quot;&lt;b&gt;</a>'
    )


def test_valueless_link_attributes() -> None:
    parser = FediverseHtmlParser("<a href>x</a>")
    assert parser.html == '<a href="#" rel="nofollow">x</a>'
    parser = FediverseHtmlParser('<a class href="https://a.example/">x</a>')
    assert parser.html == '<a href="https://a.example/" rel="nofollow">x</a>'
