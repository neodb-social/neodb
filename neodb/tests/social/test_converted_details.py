import copy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import translation

from takahe.models import Post, PreviewCard
from users.models import User

GANCIO_EVENT: dict[str, Any] = {
    "id": "https://toronto.askapunk.net/federation/m/1046",
    "type": "Event",
    "url": "https://toronto.askapunk.net/event/never-going-away-2",
    "name": "Never Going Away 2",
    "summary": "<p>St. Anne's Parish Hall, Saturday, November 14 at 04:00 PM EST</p>",
    "content": "<p>NEVER GOING AWAY SHOWCASE No.2</p>",
    "startTime": "2026-11-14T16:00:00.000-05:00",
    "location": {
        "type": "Place",
        "id": "https://toronto.askapunk.net/federation/p/170",
        "url": "https://toronto.askapunk.net/place/170/St.%20Anne's%20Parish%20Hall",
        "name": "St. Anne's Parish Hall",
        "address": "651 Dufferin Street",
    },
    "attachment": {
        "type": "Document",
        "mediaType": "image/jpeg",
        "url": "https://toronto.askapunk.net/media/0ec5.jpg",
        "name": "Never Going Away 2",
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
    "icon": [
        {
            "type": "Image",
            "url": "https://tube.kockatoo.org/lazy-static/thumbnails/a737.webp",
            "width": 280,
            "height": 157,
        },
        {
            "type": "Image",
            "url": "https://tube.kockatoo.org/lazy-static/thumbnails/56d7.webp",
            "width": 850,
            "height": 480,
        },
    ],
    "embedUrl": "https://tube.kockatoo.org/videos/embed/htQQUPxNbc7Aybzv1122yU",
    "audience": "https://tube.kockatoo.org/video-channels/kde",
    "content": "Who put the K in KDE?",
}

LEMMY_PAGE: dict[str, Any] = {
    "id": "https://feddit.uk/post/57225418",
    "type": "Page",
    "name": "TV tonight: Ellie White and Natasia Demetriou's side-splitting comedy",
    "content": "<p>With guest stars including Steve Coogan</p>",
    "audience": "https://feddit.uk/c/britishtelly",
    "image": {
        "type": "Image",
        "url": "https://i.guim.co.uk/img/media/master/2318.jpg",
    },
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


def test_gancio_event():
    details = _details(GANCIO_EVENT)
    tz = timezone(timedelta(hours=-5))
    assert details == {
        "start": datetime(2026, 11, 14, 16, 0, tzinfo=tz),
        "venue": "St. Anne's Parish Hall",
        "address": "651 Dufferin Street",
    }
    # the event's own offset is kept, not converted to UTC
    assert details["start"].utcoffset() == timedelta(hours=-5)


def test_event_end_time_same_and_other_day():
    obj = dict(GANCIO_EVENT, endTime="2026-11-14T23:00:00-05:00")
    details = _details(obj)
    assert details["end"] == datetime(2026, 11, 14, 23, 0, tzinfo=details["end"].tzinfo)
    assert details["end_same_day"] is True
    obj["endTime"] = "2026-11-15T02:00:00-05:00"
    assert _details(obj)["end_same_day"] is False
    # an end in another offset is shown in the start's offset
    obj["endTime"] = "2026-11-14T23:00:00Z"
    details = _details(obj)
    assert details["end"].utcoffset() == timedelta(hours=-5)
    assert details["end"].hour == 18
    assert details["end_same_day"] is True
    # an end before the start is dropped
    obj["endTime"] = "2026-11-14T10:00:00-05:00"
    assert "end" not in _details(obj)


def test_event_postal_address_and_coordinates():
    obj = dict(
        GANCIO_EVENT,
        location={
            "type": "Place",
            "name": "Town Hall",
            "latitude": 52.5,
            "longitude": 13.4,
            "address": {
                "type": "PostalAddress",
                "streetAddress": "Main St 1",
                "addressLocality": "Berlin",
                "postalCode": "10115",
                "addressCountry": "DE",
            },
        },
    )
    details = _details(obj)
    assert details["venue"] == "Town Hall"
    assert details["address"] == "Main St 1, Berlin, DE"


def test_online_event():
    obj = dict(
        GANCIO_EVENT,
        location={"type": "Place", "name": "https://meet.jit.si/neodb-meetup"},
    )
    details = _details(obj)
    assert details["online_url"] == "https://meet.jit.si/neodb-meetup"
    assert "venue" not in details


def test_list_valued_location_uses_first_place():
    obj = dict(
        GANCIO_EVENT,
        location=[
            "https://example.org/somewhere",
            {"type": "VirtualLocation", "name": "Stream"},
            {"type": "Place", "name": "Club"},
        ],
    )
    assert _details(obj)["venue"] == "Club"


def test_event_empty_name_and_malformed_times():
    obj: dict[str, Any] = dict(
        GANCIO_EVENT, name="", startTime="next saturday", endTime=42
    )
    obj["location"] = {"type": "Place", "name": "", "address": None}
    assert _post(obj).converted_details is None
    # strip_tags raises SuspiciousOperation on this
    bad = dict(obj, location={"type": "Place", "name": "<a" * 1000, "address": "Hall"})
    assert _post(bad).converted_details == {"address": "Hall"}
    obj["startTime"] = ["2026-11-14T16:00:00Z"]
    assert _post(obj).converted_details == {
        "start": datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
    }


def test_naive_event_time_is_utc():
    obj = dict(GANCIO_EVENT, startTime="2026-11-14T16:00:00")
    assert _details(obj)["start"].tzinfo == UTC


def test_peertube_video():
    details = _details(PEERTUBE_VIDEO)
    assert details == {
        "duration": 59,
        "duration_text": "0:59",
        "channel_url": "https://tube.kockatoo.org/video-channels/kde",
    }


@pytest.mark.parametrize(
    ("value", "seconds", "text"),
    [
        ("PT1H2M3S", 3723, "1:02:03"),
        ("PT10M", 600, "10:00"),
        ("PT2H", 7200, "2:00:00"),
        ("PT0S", None, None),
        ("PT", None, None),
        ("P1D", None, None),
        ("59", None, None),
        ("PT1.5S", None, None),
        ("PT1234567S", None, None),
        ("PT" + "9" * 4301 + "S", None, None),
        (59, None, None),
        (None, None, None),
    ],
)
def test_video_duration(value, seconds, text):
    obj = dict(PEERTUBE_VIDEO, duration=value, audience=None)
    details = _post(obj).converted_details or {}
    assert details.get("duration") == seconds
    assert details.get("duration_text") == text


def test_lemmy_page():
    details = _details(LEMMY_PAGE)
    assert details == {
        "link_url": "https://www.theguardian.com/tv-and-radio/2026/oct/21/tv-tonight",
        "link_host": "www.theguardian.com",
        "community": "c/britishtelly@feddit.uk",
        "community_url": "https://feddit.uk/c/britishtelly",
    }


def test_page_list_attachment_skips_images_and_own_host():
    obj = dict(
        LEMMY_PAGE,
        audience={"type": "Group", "id": "https://piefed.social/c/books"},
        attachment=[
            {"type": "Image", "url": "https://feddit.uk/pictrs/image/a.jpg"},
            {"type": "Link", "href": "https://feddit.uk/post/1"},
            {"type": "Link", "href": "javascript:alert(1)"},
            {"type": "Link", "href": "https://example.org/article"},
        ],
    )
    details = _details(obj)
    assert details["link_url"] == "https://example.org/article"
    assert details["community"] == "c/books@piefed.social"


def test_page_malformed_urls_are_dropped():
    obj = dict(
        LEMMY_PAGE,
        audience="https://[bad/community",
        attachment=[
            {"type": "Link", "href": "https://[bad/article"},
            {"type": "Link", "href": "https://example.org/article"},
        ],
    )
    assert _details(obj) == {
        "link_url": "https://example.org/article",
        "link_host": "example.org",
    }


def test_page_link_on_own_host_is_ignored():
    obj = dict(
        LEMMY_PAGE,
        audience="https://www.w3.org/ns/activitystreams#Public",
        attachment={"type": "Link", "href": "https://feddit.uk/post/1"},
    )
    assert _post(obj).converted_details is None


@pytest.mark.parametrize(
    "post",
    [
        Post(type=Post.Types.note, type_data={"object": GANCIO_EVENT}),
        Post(type=Post.Types.article, type_data={"object": LEMMY_PAGE}),
        Post(type=Post.Types.event, type_data=None),
        Post(type=Post.Types.event, type_data=[]),
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


def _render(post: Post) -> str:
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    with translation.override("en"):
        return render_to_string("_event_post.html", {"post": post, "request": request})


def _remote_post(owner: User, obj: dict, card: PreviewCard | None = None) -> Post:
    url = obj.get("url") if isinstance(obj.get("url"), str) else obj["id"]
    return Post.objects.create(
        author=owner.identity.takahe_identity,
        local=False,
        object_uri=obj["id"],
        url=url,
        content=obj.get("content", ""),
        type=obj["type"],
        type_data={"object": obj},
        preview_card=card,
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )


@pytest.mark.django_db(databases="__all__")
def test_render_event():
    owner = User.register(email="event@example.com", username="eventowner")
    obj = copy.deepcopy(GANCIO_EVENT)
    obj["endTime"] = "2026-11-14T23:00:00.000-05:00"
    html = _render(_remote_post(owner, obj))
    assert "Sat, Nov. 14, 2026 · 4 p.m." in html
    assert "11 p.m." in html
    assert "(UTC-05:00)" in html
    assert 'datetime="2026-11-14T16:00:00-05:00"' in html
    assert "St. Anne&#x27;s Parish Hall" in html
    assert "651 Dufferin Street" in html

    obj["id"] += "-online"
    obj["location"] = {"type": "Place", "name": "https://meet.jit.si/x"}
    html = _render(_remote_post(owner, obj))
    assert 'href="https://meet.jit.si/x"' in html
    assert ">Online</a>" in html


@pytest.mark.django_db(databases="__all__")
def test_render_video_duration_badge():
    owner = User.register(email="video@example.com", username="videoowner")
    card = PreviewCard.objects.create(
        state="fetched",
        url="https://tube.kockatoo.org/w/htQQUPxNbc7Aybzv1122yU",
        title=PEERTUBE_VIDEO["name"],
        card_type=PreviewCard.CardTypes.video,
        provider_name="tube.kockatoo.org",
        image_url="https://tube.kockatoo.org/lazy-static/thumbnails/a737.webp",
    )
    html = _render(_remote_post(owner, PEERTUBE_VIDEO, card))
    assert 'class="converted-object-duration"' in html
    assert ">0:59</span>" in html
    assert "<iframe" not in html
    assert "converted-object-details" not in html


@pytest.mark.django_db(databases="__all__")
def test_render_page_link_and_community():
    owner = User.register(email="page@example.com", username="pageowner")
    card = PreviewCard.objects.create(
        state="fetched",
        url=LEMMY_PAGE["id"],
        title=LEMMY_PAGE["name"],
        card_type=PreviewCard.CardTypes.link,
        provider_name="feddit.uk",
    )
    html = _render(_remote_post(owner, LEMMY_PAGE, card))
    # the card still points at the post, the details line at the article
    assert f'href="{LEMMY_PAGE["id"]}"' in html
    assert f'href="{LEMMY_PAGE["attachment"]["href"]}"' in html
    assert ">www.theguardian.com</a>" in html
    assert 'href="https://feddit.uk/c/britishtelly"' in html
    assert ">c/britishtelly@feddit.uk</a>" in html


@pytest.mark.django_db(databases="__all__")
def test_render_plain_converted_post_unchanged():
    owner = User.register(email="plain@example.com", username="plainowner")
    obj = {"id": "https://remote.test/objects/img", "type": "Image", "name": "x"}
    html = _render(_remote_post(owner, obj))
    assert "converted-object-details" not in html
    assert "converted-object-duration" not in html


@pytest.mark.django_db(databases="__all__")
def test_render_article_hashtag_chips():
    owner = User.register(email="tags@example.com", username="tagsowner")
    post = Post.objects.create(
        author=owner.identity.takahe_identity,
        local=False,
        object_uri="https://remote.test/articles/tags",
        url="https://remote.test/articles/tags",
        content="<p>Body</p>",
        type=Post.Types.article,
        type_data={
            "object": {
                "type": "Article",
                "name": "Tagged",
                "tag": [
                    {
                        "type": "Hashtag",
                        "name": "#linked",
                        "href": "https://remote.test/tags/linked",
                    },
                    {"type": "Hashtag", "name": "#plain"},
                ],
            }
        },
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )
    html = render_to_string(
        "_remote_article_teaser.html", {"post": post, "show_full": True}
    )
    assert 'href="https://remote.test/tags/linked"' in html
    assert '<span class="tag-chip">#plain</span>' in html
    assert "<a>" not in html
