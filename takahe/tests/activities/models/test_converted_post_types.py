"""Inbound support for Mastodon-compatible converted ActivityStreams objects."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from activities.models import Post
from activities.models.post import PostStates
from core.ld import format_ld_date
from users.models import InboxMessage
from users.models.inbox_message import InboxMessageStates


CONVERTED_TYPES = [
    Post.Types.page,
    Post.Types.image,
    Post.Types.audio,
    Post.Types.video,
    Post.Types.event,
]


@pytest.mark.parametrize("post_type", CONVERTED_TYPES)
@pytest.mark.parametrize(
    ("activity_type", "handler_name"),
    [("Create", "handle_create_ap"), ("Update", "handle_update_ap")],
)
def test_inbox_routes_converted_object_type_arrays(
    post_type,
    activity_type,
    handler_name,
):
    actor = "https://remote.test/users/alice"
    message = {
        "type": activity_type,
        "actor": actor,
        "object": {
            "id": f"https://remote.test/objects/{post_type.lower()}",
            "type": ["Document", post_type],
            "attributedTo": actor,
        },
    }
    inbox_message = InboxMessage(message=message)

    with patch.object(Post, handler_name) as handler:
        result = InboxMessageStates.handle_received(inbox_message)

    assert result == InboxMessageStates.processed
    handler.assert_called_once_with(message)


def converted_object(remote_identity, post_type):
    slug = post_type.lower()
    return {
        "id": f"https://remote.test/objects/{slug}",
        "type": [post_type, "Document"],
        "attributedTo": remote_identity.actor_uri,
        "to": "as:Public",
        "name": f"{post_type} title",
        "content": f"<p>{post_type} body</p>",
        "summary": f"<p>{post_type} summary</p>",
        "url": [
            {
                "type": "Link",
                "href": f"https://remote.test/media/{slug}",
                "mediaType": f"{slug}/example",
            },
            {
                "type": "Link",
                "href": f"https://remote.test/view/{slug}",
                "mediaType": "text/html; charset=utf-8",
            },
        ],
        "icon": {
            "type": "Image",
            "url": f"https://remote.test/icons/{slug}.png",
            "mediaType": "image/png",
            "width": 640,
            "height": 360,
        },
        "published": "2026-07-18T12:00:00Z",
        "startTime": "2026-07-20T18:00:00Z",
        "endTime": "2026-07-20T20:00:00Z",
        "location": {"type": "Place", "name": "Remote Hall"},
    }


@pytest.mark.django_db
@pytest.mark.parametrize("post_type", CONVERTED_TYPES)
def test_by_ap_converts_supported_object_to_mastodon_status(
    config_system,
    remote_identity,
    post_type,
    api_client,
    client,
):
    data = converted_object(remote_identity, post_type)

    post = Post.by_ap(data=data, create=True)

    assert post.type == post_type
    assert post.url == f"https://remote.test/view/{post_type.lower()}"
    assert post.summary is None
    assert f"<p>{post_type} body</p>" in post.content
    assert f"<p>{post_type} title</p>" in post.content
    assert f"<p>{post_type} summary</p>" in post.content
    assert post.url in post.content
    assert "limited support" not in post.safe_content_local()

    # The complete normalized object remains available for Event fields,
    # media metadata, round-tripping, and NeoDB-aware clients.
    assert post.type_data["object"]["type"] == post_type
    assert post.type_data["object"]["startTime"] == "2026-07-20T18:00:00Z"
    assert post.type_data["object"]["location"]["name"] == "Remote Hall"

    status = post.to_mastodon_json()
    assert f"{post_type} body" in status["content"]
    assert f"{post_type} title" in status["content"]
    assert f"{post_type} summary" in status["content"]
    assert post.url in status["content"]
    assert status["spoiler_text"] == ""
    assert status["ext_neodb"]["type"] == post_type
    assert status["media_attachments"] == []

    assert status["card"]["url"] == post.url
    assert status["card"]["title"] == f"{post_type} title"
    assert status["card"]["description"] == f"{post_type} summary"
    assert status["card"]["width"] == 640
    assert status["card"]["height"] == 360
    assert post.preview_card.image_url.endswith(f"/icons/{post_type.lower()}.png")
    assert f"/proxy/preview_card/{post.preview_card_id}/" in status["card"]["image"]
    expected_card_type = (
        "photo"
        if post_type == Post.Types.image
        else "video"
        if post_type == Post.Types.video
        else "link"
    )
    assert status["card"]["type"] == expected_card_type

    api_response = api_client.get(f"/api/v1/statuses/{post.pk}")
    assert api_response.status_code == 200
    api_status = api_response.json()
    assert api_status["ext_neodb"]["type"] == post_type
    assert api_status["card"]["title"] == f"{post_type} title"
    assert api_status["poll"] is None

    page = client.get(f"/@{remote_identity.handle}/posts/{post.pk}/")
    assert page.status_code == 200
    html = page.content.decode()
    assert 'class="converted-object-card ' in html
    assert f"{post_type} title" in html
    assert f"{post_type} summary" in html
    assert f"/proxy/preview_card/{post.preview_card_id}/" in html


@pytest.mark.django_db
def test_question_renders_in_api_and_takahe_page(
    config_system,
    identity,
    api_client,
    client,
):
    post = Post.create_local(
        author=identity,
        content="<p>Choose a migration route</p>",
        question={
            "type": "Question",
            "mode": "oneOf",
            "options": [
                {"name": "Route A", "type": "Note", "votes": 2},
                {"name": "Route B", "type": "Note", "votes": 1},
            ],
            "voter_count": 3,
            "end_time": format_ld_date(timezone.now() + timedelta(hours=1)),
        },
    )

    api_response = api_client.get(f"/api/v1/statuses/{post.pk}")
    assert api_response.status_code == 200
    poll = api_response.json()["poll"]
    assert poll["id"] == str(post.pk)
    assert [option["title"] for option in poll["options"]] == ["Route A", "Route B"]
    assert poll["votes_count"] == 3

    page = client.get(f"/@{identity.handle}/posts/{post.pk}/")
    assert page.status_code == 200
    html = page.content.decode()
    assert "Choose a migration route" in html
    assert "Route A" in html
    assert "Route B" in html
    assert "Sign in to vote" in html
    assert 'name="choices"' in html
    assert "disabled" in html


@pytest.mark.django_db
def test_converted_object_falls_back_to_name_and_object_id(
    config_system,
    remote_identity,
):
    data = {
        "id": "https://remote.test/objects/page-name-only",
        "type": "Page",
        "attributedTo": remote_identity.actor_uri,
        "nameMap": {"en": "Name-only page"},
        "to": "as:Public",
    }

    post = Post.by_ap(data=data, create=True)

    assert post.url == data["id"]
    assert "Name-only page" in post.content
    assert data["id"] in post.content
    assert post.language == "en"


@pytest.mark.django_db
def test_converted_object_normalizes_link_shaped_attachment(
    config_system,
    remote_identity,
):
    data = converted_object(remote_identity, Post.Types.video)
    data["attachment"] = {
        "type": "Image",
        "url": {
            "type": "Link",
            "href": "https://remote.test/media/preview.png",
            "mediaType": "image/png",
        },
        "summary": "Video preview",
        "width": "800",
        "height": "450",
    }

    post = Post.by_ap(data=data, create=True)
    attachment = post.attachments.get()

    assert attachment.remote_url == "https://remote.test/media/preview.png"
    assert attachment.mimetype == "image/png"
    assert attachment.name == "Video preview"
    assert attachment.width == 800
    assert attachment.height == 450


@pytest.mark.django_db
def test_converted_object_update_replaces_preserved_data(
    config_system,
    remote_identity,
):
    data = converted_object(remote_identity, Post.Types.event)
    post = Post.by_ap(data=data, create=True)

    data["content"] = "<p>Updated event body</p>"
    data["name"] = "Updated event title"
    data["updated"] = "2026-07-19T12:00:00Z"
    updated = Post.by_ap(data=data, update=True)

    assert updated.pk == post.pk
    assert "Updated event body" in updated.content
    assert updated.type_data["object"]["name"] == "Updated event title"
    assert updated.edited.isoformat() == "2026-07-19T12:00:00+00:00"


@pytest.mark.django_db
def test_converted_card_survives_new_and_edited_stator_handlers(
    config_system,
    remote_identity,
):
    data = converted_object(remote_identity, Post.Types.page)
    tracked_url = "https://remote.test/view/page?utm_source=fediverse"
    data["url"] = {
        "type": "Link",
        "href": tracked_url,
        "mediaType": "text/html",
    }

    post = Post.by_ap(data=data, create=True)
    original_card_id = post.preview_card_id

    assert original_card_id is not None
    assert post.preview_card.url == tracked_url
    assert post.preview_card.state == "fetched"

    with (
        patch.object(PostStates, "targets_fan_out"),
        patch.object(Post, "ensure_hashtags"),
    ):
        assert PostStates.handle_new(post) == PostStates.fanned_out
        assert PostStates.handle_edited(post) == PostStates.edited_fanned_out

    post.refresh_from_db()
    clean_url = type(post.preview_card).strip_tracking_params(tracked_url)
    assert post.preview_card_id == original_card_id
    assert post.preview_card.image_url.endswith("/icons/page.png")
    assert not type(post.preview_card).objects.filter(url=clean_url).exists()
