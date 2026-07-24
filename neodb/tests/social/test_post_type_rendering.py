from datetime import timedelta

import pytest
from django.template.loader import render_to_string
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
    # page objects reorder the body so the linked page card renders first
    assert "pagecard-order" in html
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


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_single_article_renders_reading_view():
    owner = User.register(email="article-owner@example.com", username="articleowner")
    post = Post.objects.create(
        author=owner.identity.takahe_identity,
        local=True,
        object_uri="https://remote.test/articles/hello",
        url="https://remote.test/articles/hello",
        content="<p>The full body of the article.</p><p>Second paragraph.</p>",
        type=Post.Types.article,
        type_data={
            "object": {
                "id": "https://remote.test/articles/hello",
                "type": "Article",
                "name": "Hello Fediverse",
                "summary": "A short standfirst.",
                "image": {"type": "Image", "url": "https://remote.test/cover.jpg"},
                "tag": [
                    {"type": "Hashtag", "name": "#fediverse"},
                    {"type": "Mention", "name": "@someone"},
                ],
            }
        },
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )

    response = Client().get(f"/@{owner.username}/posts/{post.pk}/")

    assert response.status_code == 200
    html = response.content.decode()
    # full reading view, not the compact timeline teaser
    assert 'class="remote-article"' in html
    assert 'class="article-teaser"' not in html
    # title renders exactly once as the reading-view heading
    assert html.count("remote-article-title") == 1
    assert "Hello Fediverse" in html
    assert "A short standfirst." in html
    assert "wrote an article" in html
    # lead image (also surfaced as the OpenGraph image), body, and hashtag
    assert "https://remote.test/cover.jpg" in html
    assert 'property="og:image" content="https://remote.test/cover.jpg"' in html
    assert "The full body of the article." in html
    assert "#fediverse" in html
    # non-hashtag tags (mentions) are filtered out of the tag footer
    assert "@someone" not in html


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_article_timeline_teaser_links_to_detail_and_truncates():
    owner = User.register(email="teaser-owner@example.com", username="teaserowner")
    body = " ".join(f"word{i}" for i in range(100))
    post = Post.objects.create(
        author=owner.identity.takahe_identity,
        local=True,
        object_uri="https://remote.test/articles/long",
        url="https://remote.test/articles/long",
        content=f"<p>{body}</p>",
        type=Post.Types.article,
        type_data={"object": {"type": "Article", "name": "Long Read"}},
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )

    html = render_to_string(
        "_remote_article_teaser.html", {"post": post, "show_full": False}
    )

    assert "article-teaser" in html
    assert "remote-article" not in html  # dedicated reading view not used inline
    assert "Long Read" in html
    assert "word0" in html
    assert "word99" not in html  # truncated at 60 words
    # teaser title links to our detail page, not the external original
    assert f"/posts/{post.pk}/" in html
    assert "https://remote.test/articles/long" not in html


@pytest.mark.parametrize(
    "image,expected",
    [
        ("https://remote.test/a.jpg", "https://remote.test/a.jpg"),
        (
            {"type": "Image", "url": "https://remote.test/b.jpg"},
            "https://remote.test/b.jpg",
        ),
        (
            {"url": {"type": "Link", "href": "https://remote.test/c.jpg"}},
            "https://remote.test/c.jpg",
        ),
        (
            {"type": "Link", "href": "https://remote.test/f.jpg"},
            "https://remote.test/f.jpg",
        ),
        (
            [{"url": "https://remote.test/d.jpg"}, "ignored"],
            "https://remote.test/d.jpg",
        ),
        (None, None),
        ({}, None),
        ("ftp://remote.test/e.jpg", None),
    ],
)
def test_article_cover_url_normalizes_image_shapes(image, expected):
    post = Post(type=Post.Types.article, type_data={"object": {"image": image}})
    assert post.article_cover_url == expected


def test_article_cover_url_none_for_non_article():
    post = Post(
        type=Post.Types.note,
        type_data={"object": {"image": "https://remote.test/x.jpg"}},
    )
    assert post.article_cover_url is None


def test_article_cover_url_handles_non_dict_type_data():
    # type_data may be any JSON shape; a list must not raise AttributeError.
    assert Post(type=Post.Types.article, type_data=[]).article_cover_url is None
    assert (
        Post(type=Post.Types.article, type_data={"object": []}).article_cover_url
        is None
    )
