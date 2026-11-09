"""Event, Video and Page details of converted objects in the Takahē web UI."""

import copy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from activities.models import Post

GANCIO_EVENT: dict[str, Any] = {
    "id": "https://toronto.askapunk.net/federation/m/1046",
    "type": "Event",
    "url": "https://toronto.askapunk.net/event/never-going-away-2",
    "name": "Never Going Away 2",
    "content": "<p>NEVER GOING AWAY SHOWCASE No.2</p>",
    "startTime": "2026-11-14T16:00:00.000-05:00",
    "location": {
        "type": "Place",
        "name": "St. Anne's Parish Hall",
        "address": "651 Dufferin Street",
    },
}

PEERTUBE_VIDEO: dict[str, Any] = {
    "id": "https://tube.kockatoo.org/videos/watch/857566ae",
    "type": "Video",
    "name": "What Does the 'K' in KDE Stand For?",
    "duration": "PT59S",
    "url": [
        {
            "type": "Link",
            "mediaType": "text/html",
            "href": "https://tube.kockatoo.org/w/htQQUPxNbc7Aybzv1122yU",
        }
    ],
    "icon": {
        "type": "Image",
        "url": "https://tube.kockatoo.org/lazy-static/thumbnails/56d7.webp",
        "width": 850,
        "height": 480,
    },
    "audience": "https://tube.kockatoo.org/video-channels/kde",
    "content": "Who put the K in KDE?",
}

LEMMY_PAGE: dict[str, Any] = {
    "id": "https://feddit.uk/post/57225418",
    "type": "Page",
    "name": "TV tonight",
    "content": "<p>With guest stars including Steve Coogan</p>",
    "audience": "https://feddit.uk/c/britishtelly",
    "attachment": {
        "type": "Link",
        "href": "https://www.theguardian.com/tv-and-radio/2026/oct/21/tv-tonight",
        "mediaType": "text/html; charset=utf-8",
    },
}


def _post(obj: dict) -> Post:
    return Post(
        type=obj["type"],
        object_uri=obj["id"],
        url=obj["url"] if isinstance(obj.get("url"), str) else obj["id"],
        type_data={"object": obj},
    )


def _details(obj: dict) -> dict[str, Any]:
    details = _post(obj).converted_details
    assert details is not None
    return details


def test_event_details():
    assert _details(GANCIO_EVENT) == {
        "start": datetime(2026, 11, 14, 16, 0, tzinfo=timezone(timedelta(hours=-5))),
        "venue": "St. Anne's Parish Hall",
        "address": "651 Dufferin Street",
    }


def test_event_end_time():
    obj = dict(GANCIO_EVENT, endTime="2026-11-14T23:00:00-05:00")
    assert _details(obj)["end_same_day"] is True
    obj["endTime"] = "2026-11-15T02:00:00-05:00"
    assert _details(obj)["end_same_day"] is False
    obj["endTime"] = "2026-11-14T23:00:00Z"
    details = _details(obj)
    assert details["end"].utcoffset() == timedelta(hours=-5)
    assert details["end"].hour == 18
    obj["endTime"] = "2026-11-14T10:00:00-05:00"
    assert "end" not in _details(obj)


def test_event_location_shapes():
    obj = dict(
        GANCIO_EVENT,
        location={
            "type": "Place",
            "name": "Town Hall",
            "address": {
                "type": "PostalAddress",
                "streetAddress": "Main St 1",
                "addressLocality": "Berlin",
                "addressCountry": "DE",
            },
        },
    )
    assert _details(obj)["address"] == "Main St 1, Berlin, DE"
    obj["location"] = [
        "https://example.org/somewhere",
        {"type": "VirtualLocation", "name": "Stream"},
        {"type": "Place", "name": "Club"},
    ]
    assert _details(obj)["venue"] == "Club"
    obj["location"] = {"type": "Place", "name": "https://meet.jit.si/x"}
    details = _details(obj)
    assert details["online_url"] == "https://meet.jit.si/x"
    assert "venue" not in details


def test_event_malformed_values():
    obj: dict[str, Any] = dict(GANCIO_EVENT, startTime="next saturday", endTime=42)
    obj["location"] = {"type": "Place", "name": "", "address": None}
    assert _post(obj).converted_details is None
    obj["startTime"] = "2026-11-14T16:00:00"
    assert _post(obj).converted_details == {
        "start": datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
    }


@pytest.mark.parametrize(
    ("value", "seconds", "text"),
    [
        ("PT59S", 59, "0:59"),
        ("PT1H2M3S", 3723, "1:02:03"),
        ("PT10M", 600, "10:00"),
        ("PT0S", None, None),
        ("PT", None, None),
        ("P1D", None, None),
        ("PT1.5S", None, None),
        ("PT" + "9" * 4301 + "S", None, None),
        (59, None, None),
    ],
)
def test_video_duration(value, seconds, text):
    obj = dict(PEERTUBE_VIDEO, duration=value, audience=None)
    details = _post(obj).converted_details or {}
    assert details.get("duration") == seconds
    assert details.get("duration_text") == text


def test_page_link_and_community():
    assert _details(LEMMY_PAGE) == {
        "link_url": LEMMY_PAGE["attachment"]["href"],
        "link_host": "www.theguardian.com",
        "community": "c/britishtelly@feddit.uk",
        "community_url": "https://feddit.uk/c/britishtelly",
    }
    obj = dict(
        LEMMY_PAGE,
        audience="https://www.w3.org/ns/activitystreams#Public",
        attachment=[
            {"type": "Link", "href": "https://feddit.uk/post/1"},
            {"type": "Link", "href": "javascript:alert(1)"},
            {"type": "Link", "href": "https://[bad/article"},
        ],
    )
    assert _post(obj).converted_details is None


@pytest.mark.parametrize(
    "post",
    [
        Post(type=Post.Types.note, type_data={"object": GANCIO_EVENT}),
        Post(type=Post.Types.event, type_data=None),
        Post(type=Post.Types.event, type_data={"object": "x"}),
        Post(type=Post.Types.image, type_data={"object": {"type": "Image"}}),
    ],
)
def test_no_details(post):
    assert post.converted_details is None


def test_article_hashtags():
    post = Post(
        type=Post.Types.article,
        type_data={
            "object": {
                "tag": [
                    {
                        "type": "Hashtag",
                        "name": "#fediverse",
                        "href": "https://remote.test/tags/fediverse",
                    },
                    {"type": "Hashtag", "name": "#nohref"},
                    {"type": "Hashtag", "name": "#bad", "href": "javascript:x"},
                    {"type": "Hashtag", "name": ""},
                    {"type": "Mention", "name": "@someone"},
                    "junk",
                ]
            }
        },
    )
    assert post.article_hashtags == [
        ("#fediverse", "https://remote.test/tags/fediverse"),
        ("#nohref", None),
        ("#bad", None),
    ]
    assert Post(type=Post.Types.article, type_data=[]).article_hashtags == []


def _page_html(client, remote_identity, obj: dict) -> str:
    data = copy.deepcopy(obj)
    # by_ap requires the object on its author's host
    data["id"] = "https://remote.test/objects/" + obj["id"].rsplit("/", 1)[-1]
    data["attributedTo"] = remote_identity.actor_uri
    data["to"] = "as:Public"
    post = Post.by_ap(data=data, create=True)
    response = client.get(f"/@{remote_identity.handle}/posts/{post.pk}/")
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_render_event(config_system, remote_identity, client):
    obj = dict(GANCIO_EVENT, endTime="2026-11-14T23:00:00.000-05:00")
    html = _page_html(client, remote_identity, obj)
    assert 'class="converted-object-details"' in html
    assert 'datetime="2026-11-14T16:00:00-05:00"' in html
    assert 'datetime="2026-11-14T23:00:00-05:00"' in html
    assert "(UTC-05:00)" in html
    assert "St. Anne&#x27;s Parish Hall" in html
    assert "651 Dufferin Street" in html

    obj = dict(
        GANCIO_EVENT,
        id="https://remote.test/objects/online",
        location={"type": "Place", "name": "https://meet.jit.si/x"},
    )
    html = _page_html(client, remote_identity, obj)
    assert 'href="https://meet.jit.si/x"' in html
    assert ">Online</a>" in html


@pytest.mark.django_db
def test_render_video_duration_badge(config_system, remote_identity, client):
    html = _page_html(client, remote_identity, PEERTUBE_VIDEO)
    assert 'class="converted-object-duration"' in html
    assert ">0:59</span>" in html
    assert "converted-object-details" not in html


@pytest.mark.django_db
def test_render_page_link_and_community(config_system, remote_identity, client):
    html = _page_html(client, remote_identity, LEMMY_PAGE)
    assert f'href="{LEMMY_PAGE["attachment"]["href"]}"' in html
    assert ">www.theguardian.com</a>" in html
    assert 'href="https://feddit.uk/c/britishtelly"' in html
    assert ">c/britishtelly@feddit.uk</a>" in html


@pytest.mark.django_db
def test_render_article_drops_unsafe_tag_href(remote_identity):
    post = Post.by_ap(
        data={
            "id": "https://remote.test/posts/article-tags/",
            "type": "Article",
            "name": "Tags",
            "content": "<p>Body</p>",
            "tag": [
                {"type": "Hashtag", "name": "#bad", "href": "javascript:alert(1)"},
                {"type": "Hashtag", "name": "#ok", "href": "https://remote.test/t/"},
            ],
            "attributedTo": "https://remote.test/test-actor/",
            "published": "2026-05-08T11:35:17Z",
        },
        create=True,
    )
    web = post.safe_content_local()
    assert "javascript:" not in web
    assert '<span class="tag">#bad</span>' in web
    assert 'href="https://remote.test/t/"' in web
