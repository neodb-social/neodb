import base64
import json
from email.utils import format_datetime

import pytest
from activities.models import Post
from core.signatures import HttpSignature, LDSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.utils import timezone

from users.models import InboxMessage


def _local_thread_uri(identity) -> str:
    """Creates a local post to reply to, returning its object URI."""
    post = Post.create_local(author=identity, content="<p>Thread</p>")
    return Post.objects.get(pk=post.pk).object_uri


def _document(actor_uri, in_reply_to=None):
    obj = {
        "id": f"{actor_uri}posts/1",
        "type": "Note",
        "content": "Hello",
        "attributedTo": actor_uri,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
    }
    if in_reply_to:
        obj["inReplyTo"] = in_reply_to
    return {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ],
        "id": f"{actor_uri}activities/create/1",
        "type": "Create",
        "actor": actor_uri,
        "object": obj,
    }


def _ld_sign_and_post(client, identity, remote_identity, keypair, document):
    key_id = f"{remote_identity.actor_uri}#main-key"
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = key_id
    remote_identity.save()
    document["signature"] = LDSignature.create_signature(
        document, keypair["private_key"], key_id
    )
    return client.post(
        identity.inbox_uri,
        data=json.dumps(document),
        content_type="application/activity+json",
    )


@pytest.mark.django_db
def test_ld_signed_reply_keeps_raw_document(
    client, identity, remote_identity, keypair, config_system
):
    """
    The inbox must keep the original (pre-canonicalisation) document of an
    LD-signed reply so it can later be forwarded verbatim (AP 7.1.2).
    """
    document = _document(
        remote_identity.actor_uri,
        in_reply_to=_local_thread_uri(identity),
    )
    resp = _ld_sign_and_post(client, identity, remote_identity, keypair, document)
    assert resp.status_code == 202
    message = InboxMessage.objects.last()
    assert message.raw_document == document
    assert "signature" in message.raw_document


@pytest.mark.django_db
def test_ld_signed_non_reply_does_not_keep_raw_document(
    client, identity, remote_identity, keypair
):
    document = _document(remote_identity.actor_uri)
    resp = _ld_sign_and_post(client, identity, remote_identity, keypair, document)
    assert resp.status_code == 202
    message = InboxMessage.objects.last()
    assert message.raw_document is None


def _http_sign_and_post(client, identity, document, keypair):
    """HTTP-sign a document as its actor and post it to the inbox."""
    body = json.dumps(document).encode()
    path = identity.inbox_uri.replace("https://example.com", "")
    digest = HttpSignature.calculate_digest(body)
    date_str = format_datetime(timezone.now(), usegmt=True)
    headers_string = "\n".join(
        f"{h}: {v}"
        for h, v in [
            ("(request-target)", f"post {path}"),
            ("host", "example.com"),
            ("date", date_str),
            ("digest", digest),
            ("content-type", "application/activity+json"),
        ]
    )
    private_key = serialization.load_pem_private_key(
        keypair["private_key"].encode(), password=None
    )
    signature_b64 = base64.b64encode(
        private_key.sign(headers_string.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    sig_header = (
        f'keyId="{document["actor"]}#main-key",'
        f'headers="(request-target) host date digest content-type",'
        f'signature="{signature_b64}",'
        f'algorithm="rsa-sha256"'
    )
    return client.post(
        identity.inbox_uri,
        data=body,
        content_type="application/activity+json",
        HTTP_HOST="example.com",
        HTTP_DATE=date_str,
        HTTP_DIGEST=digest,
        HTTP_SIGNATURE=sig_header,
    )


@pytest.mark.django_db
def test_forged_ld_signature_not_kept_despite_valid_http_signature(
    client, identity, remote_identity, keypair, config_system
):
    """
    A valid HTTP signature authenticates delivery, but a reply whose LD
    signature does not verify must not be kept for forwarding: we would
    amplify a document every receiver rejects.
    """
    key_id = f"{remote_identity.actor_uri}#main-key"
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = key_id
    remote_identity.save()
    document = _document(
        remote_identity.actor_uri,
        in_reply_to=_local_thread_uri(identity),
    )
    document["signature"] = {
        "type": "RsaSignature2017",
        "creator": key_id,
        "created": "2026-07-01T10:00:00Z",
        "signatureValue": base64.b64encode(b"forged").decode(),
    }
    resp = _http_sign_and_post(client, identity, document, keypair)
    assert resp.status_code == 202
    message = InboxMessage.objects.last()
    assert message.raw_document is None


@pytest.mark.django_db
def test_valid_ld_signature_kept_alongside_http_signature(
    client, identity, remote_identity, keypair, config_system
):
    """
    When both signatures are valid, the raw document is kept even though
    HTTP verification alone would have skipped LD verification.
    """
    key_id = f"{remote_identity.actor_uri}#main-key"
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = key_id
    remote_identity.save()
    document = _document(
        remote_identity.actor_uri,
        in_reply_to=_local_thread_uri(identity),
    )
    document["signature"] = LDSignature.create_signature(
        document, keypair["private_key"], key_id
    )
    resp = _http_sign_and_post(client, identity, document, keypair)
    assert resp.status_code == 202
    message = InboxMessage.objects.last()
    assert message.raw_document == document


@pytest.mark.django_db
def test_reply_to_remote_thread_does_not_keep_raw_document(
    client, identity, remote_identity, keypair
):
    """
    Replies to threads that are not ours are never kept: forwarding is the
    thread host's job, and keeping every signed reply would bloat the
    inbox queue on well-connected instances.
    """
    document = _document(
        remote_identity.actor_uri,
        in_reply_to="https://elsewhere.test/posts/1/",
    )
    resp = _ld_sign_and_post(client, identity, remote_identity, keypair, document)
    assert resp.status_code == 202
    message = InboxMessage.objects.last()
    assert message.raw_document is None
