import pytest
from activities.models import Post, PostAttachment
from core.uris import proxy_capability
from django.conf import settings
from django.test import override_settings

IMAGE_URL = "https://remote.test/media/secret.png"


def _attachment(remote_identity, visibility) -> PostAttachment:
    post = Post.objects.create(
        author=remote_identity,
        content="<p>hello</p>",
        local=False,
        visibility=visibility,
        object_uri=f"https://remote.test/posts/{visibility}/",
    )
    return post.attachments.create(remote_url=IMAGE_URL, mimetype="image/png")


def _serve_image(httpx_mock, times: int = 1) -> None:
    for _ in range(times):
        httpx_mock.add_response(
            url=IMAGE_URL,
            status_code=200,
            content=b"fake-png-bytes",
            headers={"Content-Type": "image/png"},
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "visibility", [Post.Visibilities.followers, Post.Visibilities.mentioned]
)
def test_private_attachment_needs_capability(
    client, remote_identity, httpx_mock, visibility
):
    """
    Attachment ids are sequential: an id alone, or a guessed last segment,
    must not reach the image of a followers-only or direct post.
    """
    attachment = _attachment(remote_identity, visibility)
    base = f"/proxy/post_attachment/{attachment.pk}/"
    assert client.get(base).status_code == 404
    assert client.get(base + "0123456789.png").status_code == 404
    other = proxy_capability("post_attachment", attachment.pk + 1)
    assert client.get(base + other + ".png").status_code == 404
    assert not httpx_mock.get_requests()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "visibility", [Post.Visibilities.followers, Post.Visibilities.mentioned]
)
def test_private_attachment_served_from_its_own_url(
    client, remote_identity, httpx_mock, visibility
):
    """The URLs handed to API clients and templates carry the capability."""
    attachment = _attachment(remote_identity, visibility)
    _serve_image(httpx_mock, times=2)
    for url in (attachment.full_url(), attachment.thumbnail_url()):
        response = client.get(url.relative)
        assert response.status_code == 200
        assert response.content == b"fake-png-bytes"
    assert attachment.to_mastodon_json()["url"].endswith(
        proxy_capability("post_attachment", attachment.pk) + ".png"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "visibility", [Post.Visibilities.public, Post.Visibilities.unlisted]
)
def test_public_attachment_keeps_bare_and_legacy_urls(
    client, remote_identity, httpx_mock, visibility
):
    """URLs issued before capabilities existed keep working for open posts."""
    attachment = _attachment(remote_identity, visibility)
    _serve_image(httpx_mock, times=2)
    base = f"/proxy/post_attachment/{attachment.pk}/"
    assert client.get(base).status_code == 200
    assert client.get(base + "0123456789.png").status_code == 200


@pytest.mark.django_db
def test_attachment_without_post_needs_capability(client, remote_identity, httpx_mock):
    attachment = PostAttachment.objects.create(
        author=remote_identity, remote_url=IMAGE_URL, mimetype="image/png"
    )
    assert client.get(f"/proxy/post_attachment/{attachment.pk}/").status_code == 404
    assert not httpx_mock.get_requests()


@pytest.mark.django_db
def test_capability_survives_key_rotation(client, remote_identity, httpx_mock):
    """A URL signed with a retired key works while that key is a fallback."""
    attachment = _attachment(remote_identity, Post.Visibilities.followers)
    old_url = attachment.full_url().relative
    with override_settings(SECRET_KEY="rotated-key"):
        assert client.get(old_url).status_code == 404
        assert attachment.full_url().relative != old_url
    _serve_image(httpx_mock)
    with override_settings(
        SECRET_KEY="rotated-key", SECRET_KEY_FALLBACKS=["previous-key"]
    ):
        assert client.get(old_url).status_code == 404
    with override_settings(
        SECRET_KEY="rotated-key", SECRET_KEY_FALLBACKS=[settings.SECRET_KEY]
    ):
        assert client.get(old_url).status_code == 200
