import socket
from io import StringIO
from unittest.mock import patch

import httpx
import pytest

from activities.models import Post, PostStates
from activities.models.post import _attach_preview_card
from activities.models.preview_card import (
    PreviewCard,
    PreviewCardStates,
)
from core.files import SSRFAttemptError, check_url_safety
from django.core.management import call_command


# ---------------------------------------------------------------------------
# strip_tracking_params — pure unit tests, no DB
# ---------------------------------------------------------------------------


def test_strip_global_utm():
    url = "https://example.com/article?utm_source=twitter&utm_medium=social&utm_campaign=foo&keep=1"
    assert (
        PreviewCard.strip_tracking_params(url) == "https://example.com/article?keep=1"
    )


def test_strip_global_fbclid():
    url = "https://example.com/page?id=123&fbclid=abc123&gclid=xyz"
    assert PreviewCard.strip_tracking_params(url) == "https://example.com/page?id=123"


def test_strip_prefix_patterns():
    url = "https://example.com/?ga_source=x&mtm_campaign=y&pk_kwd=z&normal=1"
    assert PreviewCard.strip_tracking_params(url) == "https://example.com/?normal=1"


def test_wechat_keep_essential_strip_rest():
    from urllib.parse import unquote

    url = (
        "https://mp.weixin.qq.com/s?__biz=MzA4Njc4NDEwMw==&mid=2650950929"
        "&idx=1&sn=abc123&chksm=def&scene=21&mpshare=1&poc_token=xyz&token=tok&lang=zh_CN"
    )
    result = unquote(PreviewCard.strip_tracking_params(url))
    assert "__biz=MzA4Njc4NDEwMw==" in result
    assert "mid=2650950929" in result
    assert "idx=1" in result
    assert "sn=abc123" in result
    assert "chksm" not in result
    assert "scene" not in result
    assert "mpshare" not in result
    assert "poc_token" not in result


def test_wechat_short_url_unchanged():
    url = "https://mp.weixin.qq.com/s/AbCdEfGhIjKlMn"
    assert PreviewCard.strip_tracking_params(url) == url


def test_weibo_strips_all_params():
    url = "https://weibo.com/1234567890/AbCdEfGh?wm=3333_2001&from=timeline&sudaref=weibo.com"
    assert (
        PreviewCard.strip_tracking_params(url)
        == "https://weibo.com/1234567890/AbCdEfGh"
    )


def test_xiaohongshu_strips_all_params():
    url = "https://www.xiaohongshu.com/explore/abc123def456?xsec_token=tok&xsec_source=pc_feed"
    assert (
        PreviewCard.strip_tracking_params(url)
        == "https://www.xiaohongshu.com/explore/abc123def456"
    )


def test_tiktok_strips_all_params():
    url = "https://www.tiktok.com/@user/video/7415240528377154821?is_from_webapp=1&share_app_id=1233"
    assert (
        PreviewCard.strip_tracking_params(url)
        == "https://www.tiktok.com/@user/video/7415240528377154821"
    )


def test_douyin_strips_all_params():
    url = "https://www.douyin.com/video/7123456789012345678?region=CN&ts=1234567890&share_sign=abc"
    assert (
        PreviewCard.strip_tracking_params(url)
        == "https://www.douyin.com/video/7123456789012345678"
    )


def test_douyin_normalize_discover_modal_id():
    url = "https://www.douyin.com/discover?modal_id=7123456789012345678&region=CN"
    assert (
        PreviewCard.strip_tracking_params(url)
        == "https://www.douyin.com/video/7123456789012345678"
    )


def test_normal_url_preserves_non_tracking_params():
    url = "https://example.com/search?q=hello+world&page=2&ref=toolbar"
    result = PreviewCard.strip_tracking_params(url)
    assert "q=hello+world" in result
    assert "page=2" in result
    assert "ref" not in result


def test_url_without_params_unchanged():
    url = "https://example.com/article/some-slug"
    assert PreviewCard.strip_tracking_params(url) == url


# ---------------------------------------------------------------------------
# DB smoke test
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preview_card_create_defaults():
    card = PreviewCard.objects.create(url="https://example.com/article")
    assert card.state == "needs_fetch"
    assert card.card_type == "link"
    assert card.title == ""
    assert card.image_url == ""


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------


def _mock_getaddrinfo(ip: str):
    """Returns a mock getaddrinfo result resolving to the given IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 80))]


def test_ssrf_blocks_loopback():
    req = httpx.Request("GET", "http://localhost/admin")
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("127.0.0.1")):
        with pytest.raises(SSRFAttemptError):
            check_url_safety(req)


def test_ssrf_blocks_private_10():
    req = httpx.Request("GET", "http://internal.corp/secret")
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("10.0.0.5")):
        with pytest.raises(SSRFAttemptError):
            check_url_safety(req)


def test_ssrf_blocks_private_192_168():
    req = httpx.Request("GET", "http://router.local/")
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("192.168.1.1")):
        with pytest.raises(SSRFAttemptError):
            check_url_safety(req)


def test_ssrf_blocks_aws_metadata():
    req = httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("169.254.169.254")):
        with pytest.raises(SSRFAttemptError):
            check_url_safety(req)


def test_ssrf_raises_connect_error_for_unresolvable():
    req = httpx.Request("GET", "http://doesnotexist.invalid/")
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("not found")):
        with pytest.raises(httpx.ConnectError):
            check_url_safety(req)


def test_ssrf_allows_public_ip():
    req = httpx.Request("GET", "https://example.com/")
    # 93.184.216.34 is example.com — a real public IP
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("93.184.216.34")):
        check_url_safety(req)  # should not raise


# ---------------------------------------------------------------------------
# handle_needs_fetch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_handle_needs_fetch_success(httpx_mock, config_system):
    html = """<!DOCTYPE html>
<html><head>
<title>My Article</title>
<meta property="og:title" content="OG Title" />
<meta property="og:description" content="A great article." />
<meta property="og:image" content="https://example.com/img.jpg" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
</head><body>Hello</body></html>"""
    httpx_mock.add_response(
        url="https://example.com/article",
        headers={"Content-Type": "text/html; charset=utf-8"},
        text=html,
    )
    card = PreviewCard.objects.create(url="https://example.com/article")
    with patch(
        "socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 443))]
    ):
        result = PreviewCardStates.handle_needs_fetch(card)
    card.refresh_from_db()
    assert result == PreviewCardStates.fetched
    assert card.title == "OG Title"
    assert card.description == "A great article."
    assert card.image_url == "https://example.com/img.jpg"
    assert card.image_width == 1200
    assert card.image_height == 630
    assert card.provider_name == "example.com"
    assert card.provider_url == "https://example.com"
    assert card.fetched_at is not None


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_handle_needs_fetch_falls_back_to_title_tag(httpx_mock, config_system):
    html = '<html><head><title>Plain Title</title><meta name="description" content="Desc"/></head></html>'
    httpx_mock.add_response(
        url="https://example.com/plain",
        headers={"Content-Type": "text/html"},
        text=html,
    )
    card = PreviewCard.objects.create(url="https://example.com/plain")
    with patch(
        "socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 443))]
    ):
        result = PreviewCardStates.handle_needs_fetch(card)
    card.refresh_from_db()
    assert result == PreviewCardStates.fetched
    assert card.title == "Plain Title"
    assert card.description == "Desc"


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_handle_needs_fetch_non_html_returns_fetch_failed(httpx_mock, config_system):
    httpx_mock.add_response(
        url="https://example.com/image.png",
        headers={"Content-Type": "image/png"},
        content=b"\x89PNG",
    )
    card = PreviewCard.objects.create(url="https://example.com/image.png")
    with patch(
        "socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 443))]
    ):
        result = PreviewCardStates.handle_needs_fetch(card)
    assert result == PreviewCardStates.fetch_failed


@pytest.mark.django_db
def test_handle_needs_fetch_invalid_scheme(config_system):
    card = PreviewCard.objects.create(url="ftp://example.com/file.txt")
    result = PreviewCardStates.handle_needs_fetch(card)
    assert result == PreviewCardStates.fetch_failed


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_handle_needs_fetch_ssrf_blocked(httpx_mock, config_system):
    card = PreviewCard.objects.create(url="http://192.168.1.1/")
    with patch("socket.getaddrinfo", return_value=[(2, 1, 0, "", ("192.168.1.1", 80))]):
        result = PreviewCardStates.handle_needs_fetch(card)
    assert result == PreviewCardStates.fetch_failed


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_handle_needs_fetch_drops_oversized_image_url(httpx_mock, config_system):
    """An og:image longer than the column limit is dropped, not stored truncated."""
    long_image = "https://example.com/" + ("a" * 3000) + ".jpg"
    html = (
        "<html><head><title>T</title>"
        f'<meta property="og:image" content="{long_image}" />'
        "</head></html>"
    )
    httpx_mock.add_response(
        url="https://example.com/big-image",
        headers={"Content-Type": "text/html"},
        text=html,
    )
    card = PreviewCard.objects.create(url="https://example.com/big-image")
    with patch(
        "socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 443))]
    ):
        result = PreviewCardStates.handle_needs_fetch(card)
    card.refresh_from_db()
    assert result == PreviewCardStates.fetched
    assert card.image_url == ""


@pytest.mark.django_db
@pytest.mark.httpx_mock(
    assert_all_requests_were_expected=False, can_send_already_matched_responses=True
)
def test_handle_needs_fetch_truncates_long_author(httpx_mock, config_system):
    """An og:article:author longer than the column limit is truncated to fit."""
    long_author = "X" * 600
    html = (
        "<html><head><title>T</title>"
        f'<meta property="og:article:author" content="{long_author}" />'
        "</head></html>"
    )
    httpx_mock.add_response(
        url="https://example.com/long-author",
        headers={"Content-Type": "text/html"},
        text=html,
    )
    card = PreviewCard.objects.create(url="https://example.com/long-author")
    with patch(
        "socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 443))]
    ):
        result = PreviewCardStates.handle_needs_fetch(card)
    card.refresh_from_db()
    assert result == PreviewCardStates.fetched
    assert card.author_name == "X" * 500


# ---------------------------------------------------------------------------
# to_mastodon_json
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_to_mastodon_json_with_image(config_system):
    card = PreviewCard.objects.create(
        url="https://example.com/article",
        title="My Title",
        description="My Desc",
        card_type="link",
        provider_name="example.com",
        provider_url="https://example.com",
        image_url="https://cdn.example.com/img.jpg",
        image_width=1200,
        image_height=630,
    )
    result = card.to_mastodon_json()
    assert result["url"] == "https://example.com/article"
    assert result["title"] == "My Title"
    assert result["description"] == "My Desc"
    assert result["type"] == "link"
    assert result["provider_name"] == "example.com"
    assert result["width"] == 1200
    assert result["height"] == 630
    assert result["image"] is not None
    assert f"/proxy/preview_card/{card.pk}/" in result["image"]
    assert result["blurhash"] is None


@pytest.mark.django_db
def test_to_mastodon_json_no_image(config_system):
    card = PreviewCard.objects.create(
        url="https://example.com/no-img",
        title="No Image",
        image_url="",
    )
    result = card.to_mastodon_json()
    assert result["image"] is None
    assert result["width"] == 0
    assert result["height"] == 0


# ---------------------------------------------------------------------------
# _attach_preview_card integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_attach_preview_card_creates_and_links(identity, config_system):
    post = Post.create_local(
        author=identity,
        content='<p>Check this out: <a href="https://example.com/article">https://example.com/article</a></p>',
    )
    _attach_preview_card(post.pk, post.content)
    post.refresh_from_db()
    assert post.preview_card is not None
    assert post.preview_card.url == "https://example.com/article"
    assert post.preview_card.state == "needs_fetch"


@pytest.mark.django_db
def test_attach_preview_card_deduplicates(identity, config_system):
    existing = PreviewCard.objects.create(url="https://example.com/shared")
    post1 = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/shared">link</a></p>',
    )
    post2 = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/shared">same link</a></p>',
    )
    _attach_preview_card(post1.pk, post1.content)
    _attach_preview_card(post2.pk, post2.content)
    post1.refresh_from_db()
    post2.refresh_from_db()
    assert post1.preview_card_id == existing.pk
    assert post2.preview_card_id == existing.pk
    assert PreviewCard.objects.filter(url="https://example.com/shared").count() == 1


@pytest.mark.django_db
def test_attach_preview_card_strips_tracking(identity, config_system):
    # Single tracking param only — avoids &amp; encoding issues from HTML processing
    post = Post.create_local(
        author=identity,
        content="<p>https://example.com/art?utm_source=twitter</p>",
    )
    _attach_preview_card(post.pk, post.content)
    post.refresh_from_db()
    assert post.preview_card is not None
    assert post.preview_card.url == "https://example.com/art"


@pytest.mark.django_db
def test_attach_preview_card_clears_when_no_url(identity, config_system):
    card = PreviewCard.objects.create(url="https://example.com/old")
    post = Post.create_local(author=identity, content="<p>No links here</p>")
    Post.objects.filter(pk=post.pk).update(preview_card=card)
    _attach_preview_card(post.pk, "<p>No links here</p>")
    post.refresh_from_db()
    assert post.preview_card is None


@pytest.mark.django_db
def test_attach_preview_card_updates_last_referenced_at(identity, config_system):
    from django.utils import timezone

    before = timezone.now()
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/ts">link</a></p>',
    )
    _attach_preview_card(post.pk, post.content)
    post.refresh_from_db()
    assert post.preview_card.last_referenced_at is not None
    assert post.preview_card.last_referenced_at >= before


# ---------------------------------------------------------------------------
# Post.to_mastodon_json card field
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_post_to_mastodon_json_includes_card(identity, config_system):
    card = PreviewCard.objects.create(
        url="https://example.com/article",
        title="Card Title",
        description="Card Desc",
        state="fetched",
    )
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/article">link</a></p>',
    )
    Post.objects.filter(pk=post.pk).update(preview_card=card)
    post.refresh_from_db()
    result = post.to_mastodon_json()
    assert result["card"] is not None
    assert result["card"]["title"] == "Card Title"
    assert result["card"]["description"] == "Card Desc"


@pytest.mark.django_db
def test_post_to_mastodon_json_card_none_when_not_fetched(identity, config_system):
    card = PreviewCard.objects.create(
        url="https://example.com/pending",
        state="needs_fetch",
    )
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/pending">link</a></p>',
    )
    Post.objects.filter(pk=post.pk).update(preview_card=card)
    post.refresh_from_db()
    result = post.to_mastodon_json()
    assert result["card"] is None


@pytest.mark.django_db
def test_post_to_mastodon_json_card_none_when_no_card(identity, config_system):
    post = Post.create_local(author=identity, content="<p>No links</p>")
    result = post.to_mastodon_json()
    assert result["card"] is None


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_preview_cards_command_creates_cards(identity, config_system):
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/cmd-test">link</a></p>',
    )
    assert post.preview_card is None
    out = StringIO()
    call_command("fetch_preview_cards", days=30, stdout=out)
    post.refresh_from_db()
    assert post.preview_card is not None
    assert post.preview_card.url == "https://example.com/cmd-test"
    output = out.getvalue()
    assert "created" in output.lower() or "queued" in output.lower()


@pytest.mark.django_db
def test_fetch_preview_cards_command_skips_fetched(identity, config_system):
    card = PreviewCard.objects.create(
        url="https://example.com/done",
        state="fetched",
        title="Already Done",
    )
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/done">link</a></p>',
    )
    Post.objects.filter(pk=post.pk).update(preview_card=card)
    out = StringIO()
    call_command("fetch_preview_cards", days=30, stdout=out)
    assert (
        PreviewCard.objects.filter(
            url="https://example.com/done", state="fetched"
        ).count()
        == 1
    )
    output = out.getvalue()
    assert "skip" in output.lower()


@pytest.mark.django_db
def test_fetch_preview_cards_command_requeues_failed(identity, config_system):
    card = PreviewCard.objects.create(
        url="https://example.com/failed",
        state="fetch_failed",
    )
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/failed">link</a></p>',
    )
    Post.objects.filter(pk=post.pk).update(preview_card=card)
    out = StringIO()
    call_command("fetch_preview_cards", days=30, stdout=out)
    card.refresh_from_db()
    assert card.state == "needs_fetch"
    output = out.getvalue()
    assert "requeue" in output.lower() or "re-queue" in output.lower()


# ---------------------------------------------------------------------------
# Additional essential model tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preview_card_url_unique(config_system):
    """Two posts linking to the same canonical URL share one PreviewCard."""
    PreviewCard.objects.create(url="https://example.com/shared")
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        PreviewCard.objects.create(url="https://example.com/shared")


@pytest.mark.django_db
def test_preview_card_state_transitions(config_system):
    """needs_fetch is the initial state; transitions to fetched/fetch_failed allowed."""
    card = PreviewCard.objects.create(url="https://example.com/test-state")
    assert card.state == PreviewCardStates.needs_fetch
    card.transition_perform(PreviewCardStates.fetched)
    card.refresh_from_db()
    assert card.state == PreviewCardStates.fetched


@pytest.mark.django_db
def test_preview_card_state_transition_to_failed(config_system):
    card = PreviewCard.objects.create(url="https://example.com/test-fail")
    card.transition_perform(PreviewCardStates.fetch_failed)
    card.refresh_from_db()
    assert card.state == PreviewCardStates.fetch_failed


@pytest.mark.django_db
def test_handle_new_attaches_card_via_stator(identity, stator, config_system):
    """Stator handle_new creates and links a PreviewCard."""
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/stator-test">link</a></p>',
    )
    # Manually invoke the state handler (simulating Stator)

    PostStates.handle_new(post)
    post.refresh_from_db()
    assert post.preview_card is not None
    assert post.preview_card.url == "https://example.com/stator-test"
    assert post.preview_card.state == "needs_fetch"


@pytest.mark.django_db
def test_handle_edited_updates_card(identity, stator, config_system):
    """handle_edited re-evaluates the card when post content changes."""
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/old-url">link</a></p>',
    )

    PostStates.handle_new(post)
    post.refresh_from_db()
    old_card_pk = post.preview_card_id

    # Edit post to use a different URL
    Post.objects.filter(pk=post.pk).update(
        content='<p><a href="https://example.com/new-url">new link</a></p>'
    )
    post.refresh_from_db()
    PostStates.handle_edited(post)
    post.refresh_from_db()
    assert post.preview_card is not None
    assert post.preview_card.url == "https://example.com/new-url"
    assert post.preview_card_id != old_card_pk


@pytest.mark.django_db
def test_handle_edited_clears_card_when_url_removed(identity, stator, config_system):
    """handle_edited sets preview_card=None when the URL is removed from content."""
    post = Post.create_local(
        author=identity,
        content='<p><a href="https://example.com/removeme">link</a></p>',
    )

    PostStates.handle_new(post)
    post.refresh_from_db()
    assert post.preview_card is not None

    Post.objects.filter(pk=post.pk).update(content="<p>No links here</p>")
    post.refresh_from_db()
    PostStates.handle_edited(post)
    post.refresh_from_db()
    assert post.preview_card is None


@pytest.mark.django_db
def test_preview_card_proxy_url_format(config_system):
    """to_mastodon_json returns image as a local proxy URL, not the remote URL."""
    card = PreviewCard.objects.create(
        url="https://example.com/article",
        image_url="https://cdn.remote.example/thumb.jpg",
    )
    result = card.to_mastodon_json()
    assert result["image"] is not None
    assert "cdn.remote.example" not in result["image"], (
        "Raw remote URL must not be exposed"
    )
    assert "/proxy/preview_card/" in result["image"]


@pytest.mark.django_db
def test_strip_tracking_params_idempotent():
    """Calling strip_tracking_params twice yields the same result."""
    url = "https://example.com/page?utm_source=x&id=42"
    once = PreviewCard.strip_tracking_params(url)
    twice = PreviewCard.strip_tracking_params(once)
    assert once == twice
