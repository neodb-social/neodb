import pytest

from activities.models import FanOut, Post
from core.signatures import LDSignature, VerificationError


def _create_local_post(author, content, **kwargs) -> Post:
    """
    Creates a local post and reloads it from the database, as Stator does
    before fanning out (fresh instances carry lazy urlman values that pyld
    cannot deepcopy; delivery only ever happens on DB-loaded rows).
    """
    post = Post.create_local(author=author, content=content, **kwargs)
    return Post.objects.get(pk=post.pk)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "fan_out_type",
    [FanOut.Types.post, FanOut.Types.post_edited, FanOut.Types.post_deleted],
)
def test_public_post_fan_out_carries_ld_signature(
    identity, keypair, config_system, fan_out_type
):
    """
    Public local posts must carry a verifiable LD signature on their
    Create/Update/Delete fan-out documents so receivers can forward them.
    """
    post = _create_local_post(identity, "<p>Hello</p>")
    document = post.to_fan_out_ap(fan_out_type)
    assert document is not None
    assert document["signature"]["type"] == "RsaSignature2017"
    assert document["signature"]["creator"] == identity.public_key_id
    # Must verify against the author's public key
    LDSignature.verify_signature(document, keypair["public_key"])


@pytest.mark.django_db
def test_ld_signature_tampering_detected(identity, keypair, config_system):
    post = _create_local_post(identity, "<p>Hello</p>")
    document = post.to_fan_out_ap(FanOut.Types.post)
    document["object"]["content"] = "<p>Tampered</p>"
    with pytest.raises(VerificationError):
        LDSignature.verify_signature(document, keypair["public_key"])


@pytest.mark.django_db
def test_unlisted_post_fan_out_carries_ld_signature(identity, config_system):
    post = _create_local_post(
        identity, "<p>Hello</p>", visibility=Post.Visibilities.unlisted
    )
    document = post.to_fan_out_ap(FanOut.Types.post)
    assert "signature" in document


@pytest.mark.django_db
@pytest.mark.parametrize(
    "visibility",
    [Post.Visibilities.followers, Post.Visibilities.mentioned],
)
def test_private_post_fan_out_is_not_ld_signed(identity, config_system, visibility):
    """
    Followers-only and mentioned-only posts stay unsigned so they cannot be
    proven authentic by third parties (matching Mastodon's behaviour).
    """
    post = _create_local_post(identity, "<p>Secret</p>", visibility=visibility)
    document = post.to_fan_out_ap(FanOut.Types.post)
    assert "signature" not in document


@pytest.mark.django_db
def test_unknown_fan_out_type_returns_none(identity, config_system):
    post = _create_local_post(identity, "<p>Hello</p>")
    assert post.to_fan_out_ap(FanOut.Types.interaction) is None
