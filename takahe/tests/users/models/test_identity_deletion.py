import pytest
from django.core.files.base import ContentFile

from activities.models import Post, PostAttachment, PostAttachmentStates
from users.models import (
    AccountNote,
    Block,
    FeatureAuthorization,
    Identity,
    IdentityStates,
    List,
    Marker,
    User,
)


@pytest.fixture
def other_identity(identity_factory) -> Identity:
    return identity_factory(username="other", name="Other User")


@pytest.mark.django_db
def test_handle_deleted_removes_rows_keyed_on_the_identity(
    identity, other_identity, config_system
):
    Block.create_local_block(identity, other_identity)
    AccountNote.objects.create(source=identity, target=other_identity, note="a note")
    List.objects.create(
        identity=identity, title="a list", replies_policy="none", exclusive=False
    )
    Marker.objects.create(identity=identity, timeline="home", last_read_id="1")
    FeatureAuthorization.objects.create(
        identity=identity, collection_uri="https://example2.com/featured/"
    )

    IdentityStates.handle_deleted(identity)

    # The identity row stays as a tombstone, so nothing here cascades away.
    assert not Block.objects.filter(source=identity).exists()
    assert not AccountNote.objects.filter(source=identity).exists()
    assert not List.objects.filter(identity=identity).exists()
    assert not Marker.objects.filter(identity=identity).exists()
    assert not FeatureAuthorization.objects.filter(identity=identity).exists()


@pytest.mark.django_db
def test_mark_deleted_closes_a_login_with_no_other_identity(identity, config_system):
    user = identity.users.first()
    assert user and not user.deleted

    identity.mark_deleted()

    user.refresh_from_db()
    assert user.deleted
    assert identity.deleted is not None
    assert not identity.users.exists()


@pytest.mark.django_db
def test_mark_deleted_keeps_a_login_that_still_holds_an_identity(
    identity, other_identity, config_system
):
    user = identity.users.first()
    assert user
    assert other_identity.users.filter(pk=user.pk).exists()

    identity.mark_deleted()

    user.refresh_from_db()
    assert not user.deleted
    assert User.objects.filter(pk=user.pk, deleted=False).exists()


def _attachment(identity, post=None) -> PostAttachment:
    attachment = PostAttachment.objects.create(
        mimetype="image/webp",
        name="Test attachment",
        author=identity,
        post=post,
        width=1,
        height=1,
        state=PostAttachmentStates.fetched,
    )
    attachment.file.save("test.webp", ContentFile(b"bytes"), save=True)
    return attachment


@pytest.mark.django_db
def test_deleting_an_attachment_row_reclaims_its_file(identity, config_system):
    attachment = _attachment(identity)
    storage, name = attachment.file.storage, attachment.file.name
    assert storage.exists(name)

    attachment.delete()

    assert not storage.exists(name)


@pytest.mark.django_db
def test_handle_deleted_collects_media_never_attached_to_a_post(
    identity, config_system
):
    orphan = _attachment(identity)
    storage, name = orphan.file.storage, orphan.file.name

    IdentityStates.handle_deleted(identity)

    assert not PostAttachment.objects.filter(pk=orphan.pk).exists()
    assert not storage.exists(name)


@pytest.mark.django_db
def test_handle_deleted_keeps_post_media_so_the_delete_still_serializes(
    identity, config_system
):
    post = Post.create_local(author=identity, content="<p>bye</p>")
    attachment = _attachment(identity, post=post)

    IdentityStates.handle_deleted(identity)

    # The fanout delivers later and serializes the post, which reads the
    # attachment file; clearing it here would raise instead.
    attachment.refresh_from_db()
    assert attachment.file
    assert post.to_delete_ap()["object"]["attachment"]
