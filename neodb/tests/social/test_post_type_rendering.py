from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from takahe.models import Post, PreviewCard
from users.models import User


CONVERTED_TYPES = [
    Post.Types.page,
    Post.Types.image,
    Post.Types.audio,
    Post.Types.video,
    Post.Types.event,
]


@pytest.mark.django_db(databases="__all__", transaction=True)
@pytest.mark.parametrize("post_type", CONVERTED_TYPES)
def test_single_post_renders_converted_object_card(post_type):
    owner = User.register(
        email=f"{post_type.lower()}-owner@example.com",
        username=f"{post_type.lower()}owner",
    )
    author = owner.identity.takahe_identity
    slug = post_type.lower()
    card = PreviewCard.objects.create(
        state="fetched",
        url=f"https://remote.test/view/{slug}",
        title=f"{post_type} title",
        description=f"{post_type} summary",
        card_type=(
            PreviewCard.CardTypes.photo
            if post_type == Post.Types.image
            else PreviewCard.CardTypes.video
            if post_type == Post.Types.video
            else PreviewCard.CardTypes.link
        ),
        provider_name="remote.test",
        provider_url="https://remote.test",
        image_url=f"https://remote.test/icons/{slug}.png",
        image_width=640,
        image_height=360,
        fetched_at=timezone.now(),
    )
    post = Post.objects.create(
        author=author,
        local=True,
        object_uri=f"https://example.com/objects/{slug}",
        url=card.url,
        content=(
            f"<p>{post_type} body</p><p>{post_type} title</p><p>{post_type} summary</p>"
        ),
        type=post_type,
        type_data={
            "object": {
                "id": f"https://example.com/objects/{slug}",
                "type": post_type,
                "name": f"{post_type} title",
                "summary": f"{post_type} summary",
            }
        },
        preview_card=card,
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )

    response = Client().get(f"/@{owner.username}/posts/{post.pk}/")

    assert response.status_code == 200
    html = response.content.decode()
    assert 'class="converted-object-card ' in html
    assert f"{post_type} title" in html
    assert f"{post_type} summary" in html
    assert f"/proxy/preview_card/{card.pk}/" in html
    assert f'content="{post_type} title"' in html


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_deleted_preview_card_does_not_break_mastodon_serialization():
    owner = User.register(email="deleted-card@example.com", username="deletedcard")
    card = PreviewCard.objects.create(
        state="fetched",
        url="https://remote.test/view/deleted-card",
        title="Deleted card",
        image_url="https://remote.test/icons/deleted-card.png",
    )
    post = Post.objects.create(
        author=owner.identity.takahe_identity,
        local=True,
        object_uri="https://example.com/objects/deleted-card",
        url=card.url,
        content="<p>Deleted card body</p>",
        type=Post.Types.page,
        type_data={"object": {"type": "Page"}},
        preview_card=card,
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )
    stale_post = Post.objects.get(pk=post.pk)
    card_pk = card.pk

    card.delete()

    assert stale_post.preview_card_id == card_pk
    assert stale_post.to_mastodon_json()["card"] is None
    assert stale_post.converted_preview_card is None


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_single_question_renders_for_anonymous_and_allows_authenticated_vote():
    owner = User.register(email="poll-owner@example.com", username="pollowner")
    viewer = User.register(email="poll-viewer@example.com", username="pollviewer")
    post = Post.objects.create(
        author=owner.identity.takahe_identity,
        local=True,
        object_uri="https://example.com/objects/poll",
        url="https://example.com/objects/poll",
        content="<p>Choose a migration route</p>",
        type=Post.Types.question,
        type_data={
            "type": "Question",
            "mode": "oneOf",
            "options": [
                {"name": "Route A", "type": "Note", "votes": 2},
                {"name": "Route B", "type": "Note", "votes": 1},
            ],
            "voter_count": 3,
            "end_time": (timezone.now() + timedelta(hours=1)).isoformat(),
        },
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )
    url = f"/@{owner.username}/posts/{post.pk}/"

    anonymous_response = Client().get(url)
    assert anonymous_response.status_code == 200
    anonymous_html = anonymous_response.content.decode()
    assert "Choose a migration route" in anonymous_html
    assert "Route A" in anonymous_html
    assert "Route B" in anonymous_html
    assert "Poll options" in anonymous_html
    assert "Poll ending at" in anonymous_html
    assert "Log in to vote." in anonymous_html
    assert 'value="Route A"' in anonymous_html
    assert "disabled" in anonymous_html
    assert 'value="Submit"' not in anonymous_html
    assert 'property="og:title"' in anonymous_html
    assert "Choose a migration route" in anonymous_html

    authenticated_client = Client()
    authenticated_client.force_login(viewer, backend="mastodon.auth.OAuth2Backend")
    authenticated_response = authenticated_client.get(url)
    assert authenticated_response.status_code == 200
    authenticated_html = authenticated_response.content.decode()
    assert 'value="Submit"' in authenticated_html
    assert "Log in to vote." not in authenticated_html
