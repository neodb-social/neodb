import pytest

from activities.models import Post, PostAttachment


@pytest.mark.django_db
def test_remote_attachment_with_non_http_url_is_dropped(remote_identity, config_system):
    post = Post.by_ap(
        data={
            "id": "https://remote.test/posts/2",
            "type": "Note",
            "content": "Hi World",
            "attributedTo": remote_identity.actor_uri,
            "published": "2022-12-23T10:50:54Z",
            "attachment": [
                {
                    "type": "Document",
                    "url": "javascript:alert(1)",
                    "mediaType": "application/pdf",
                },
                {
                    "type": "Document",
                    "url": "javascript://%0aalert(1)",
                    "mediaType": "application/pdf",
                },
                {
                    "type": "Document",
                    "url": "https://remote.test/posts/2/file.pdf",
                    "mediaType": "application/pdf",
                },
            ],
        },
        create=True,
    )

    assert [a.remote_url for a in post.attachments.all()] == [
        "https://remote.test/posts/2/file.pdf"
    ]


@pytest.mark.django_db
def test_stored_non_http_attachment_url_never_becomes_an_href(remote_identity):
    attachment = PostAttachment.objects.create(
        author=remote_identity,
        mimetype="application/pdf",
        remote_url="javascript://%0aalert(1)",
    )

    assert attachment.full_url().relative.startswith("/proxy/post_attachment/")
