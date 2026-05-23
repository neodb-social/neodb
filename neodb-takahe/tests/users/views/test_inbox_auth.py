import base64
import json
from email.utils import format_datetime

import pytest
from core.signatures import HttpSignature, LDSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.utils import timezone
from users.models.inbox_message import InboxMessageStates

from users.models import Domain, Identity, InboxMessage


def _make_document(actor_uri="https://remote.test/test-actor/"):
    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_uri}activities/create/1",
        "type": "Create",
        "actor": actor_uri,
        "object": {
            "id": f"{actor_uri}posts/1",
            "type": "Note",
            "content": "Hello",
            "attributedTo": actor_uri,
        },
    }


def _post_to_inbox(client, identity, document, extra_headers=None):
    """Post a document to the identity's inbox with optional extra HTTP headers."""
    kwargs = {
        "data": json.dumps(document),
        "content_type": "application/activity+json",
    }
    if extra_headers:
        kwargs.update(extra_headers)
    return client.post(identity.inbox_uri, **kwargs)


def _sign_and_post(client, identity, document, keypair):
    """Sign a document with HTTP Signature and post it to the inbox."""
    body = json.dumps(document).encode()
    path = identity.inbox_uri.replace("https://example.com", "")
    digest = HttpSignature.calculate_digest(body)
    date_str = format_datetime(timezone.now(), usegmt=True)

    headers_to_sign = ["(request-target)", "host", "date", "digest", "content-type"]
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
    signature_bytes = private_key.sign(
        headers_string.encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = base64.b64encode(signature_bytes).decode()
    # keyId must be derived from the document actor URI so that urldefrag(keyid)
    # matches document["actor"] and direct delivery is not mistaken for a relay.
    # Preserve the trailing slash to keep the URI identical to document["actor"].
    actor_uri = document.get("actor", "")
    key_id = actor_uri + "#main-key" if actor_uri else keypair["public_key_id"]
    sig_header = (
        f'keyId="{key_id}",'
        f'headers="{" ".join(headers_to_sign)}",'
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
def test_no_signature_rejected(client, identity):
    """Messages with no signature at all are rejected with 401."""
    document = _make_document()
    resp = _post_to_inbox(client, identity, document)
    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_valid_http_signature_accepted(client, identity, remote_identity, keypair):
    """Messages with a valid HTTP Signature are accepted."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    resp = _sign_and_post(client, identity, document, keypair)
    assert resp.status_code == 202
    msg = InboxMessage.objects.last()
    assert msg is not None
    assert msg.metadata is None


@pytest.mark.django_db
def test_http_sig_passes_invalid_ld_sig_ignored(
    client, identity, remote_identity, keypair
):
    """
    When HTTP signature is valid, an invalid LD signature is ignored.
    Mastodon and other implementations attach LD signatures alongside HTTP
    signatures; pyld/ruby-jsonld URDNA2015 differences mean the LD sig can
    fail even on authentic messages. The HTTP sig alone is sufficient.
    """
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    document["signature"] = {
        "type": "RsaSignature2017",
        "creator": f"{remote_identity.actor_uri}#main-key",
        "created": "2023-10-25T08:08:47.702Z",
        "signatureValue": base64.b64encode(b"invalid_ld_sig").decode(),
    }
    resp = _sign_and_post(client, identity, document, keypair)
    assert resp.status_code == 202
    assert InboxMessage.objects.count() == 1


@pytest.mark.django_db
def test_invalid_http_signature_rejected(client, identity, remote_identity, keypair):
    """Messages with an invalid HTTP Signature (bad sig bytes) are rejected."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    body = json.dumps(document).encode()
    digest = HttpSignature.calculate_digest(body)

    date_str = format_datetime(timezone.now(), usegmt=True)

    # Use a garbage signature value
    sig_header = (
        f'keyId="{keypair["public_key_id"]}",'
        f'headers="(request-target) host date digest content-type",'
        f'signature="AAAA_invalid_signature_AAAA",'
        f'algorithm="rsa-sha256"'
    )
    resp = client.post(
        identity.inbox_uri,
        data=body,
        content_type="application/activity+json",
        HTTP_HOST="example.com",
        HTTP_DATE=date_str,
        HTTP_DIGEST=digest,
        HTTP_SIGNATURE=sig_header,
    )
    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_http_signature_unknown_actor_deferred(client, identity):
    """
    Messages from an unknown actor (no public key stored) are deferred:
    an InboxMessage is created with metadata for later verification.
    """
    actor_uri = "https://unknown.test/actor/"
    Domain.objects.create(domain="unknown.test", local=False, state="updated")
    document = _make_document(actor_uri=actor_uri)
    body = json.dumps(document).encode()
    digest = HttpSignature.calculate_digest(body)
    date_str = format_datetime(timezone.now(), usegmt=True)

    sig_header = (
        f'keyId="{actor_uri}#main-key",'
        f'headers="(request-target) host date digest content-type",'
        f'signature="{base64.b64encode(b"fakesig").decode()}",'
        f'algorithm="rsa-sha256"'
    )
    resp = client.post(
        identity.inbox_uri,
        data=body,
        content_type="application/activity+json",
        HTTP_HOST="example.com",
        HTTP_DATE=date_str,
        HTTP_DIGEST=digest,
        HTTP_SIGNATURE=sig_header,
    )
    assert resp.status_code == 202
    msg = InboxMessage.objects.last()
    assert msg is not None
    assert msg.metadata is not None
    assert "http_sig" in msg.metadata
    assert msg.metadata["http_sig"]["actor_uri"] == actor_uri


@pytest.mark.django_db
def test_valid_ld_signature_accepted(client, identity, remote_identity, keypair):
    """Messages with a valid LD Signature are accepted."""
    key_id = f"{remote_identity.actor_uri}#main-key"
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = key_id
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    signature_section = LDSignature.create_signature(
        document,
        keypair["private_key"],
        key_id,
    )
    document["signature"] = signature_section

    resp = _post_to_inbox(client, identity, document)
    assert resp.status_code == 202
    msg = InboxMessage.objects.last()
    assert msg is not None
    assert msg.metadata is None


@pytest.mark.django_db
def test_invalid_ld_signature_rejected(client, identity, remote_identity, keypair):
    """Messages with an invalid LD Signature are rejected."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    document["signature"] = {
        "creator": f"{remote_identity.actor_uri}#test-key",
        "created": "2023-10-25T08:08:47.702Z",
        "signatureValue": base64.b64encode(b"invalid_signature").decode(),
        "type": "RsaSignature2017",
    }

    resp = _post_to_inbox(client, identity, document)
    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_ld_signature_unknown_creator_deferred(client, identity):
    """
    LD Signature from an unknown creator (no key) is deferred.
    """
    actor_uri = "https://unknown.test/actor/"
    creator_uri = "https://unknown.test/actor/"
    Domain.objects.create(domain="unknown.test", local=False, state="updated")

    document = _make_document(actor_uri=actor_uri)
    document["signature"] = {
        "creator": f"{creator_uri}#main-key",
        "created": "2023-10-25T08:08:47.702Z",
        "signatureValue": base64.b64encode(b"fakesig").decode(),
        "type": "RsaSignature2017",
    }

    resp = _post_to_inbox(client, identity, document)
    assert resp.status_code == 202
    msg = InboxMessage.objects.last()
    assert msg is not None
    assert msg.metadata is not None
    assert "ld_sig" in msg.metadata
    assert msg.metadata["ld_sig"]["creator_uri"] == creator_uri


@pytest.mark.django_db
def test_malformed_ld_signature_rejected(client, identity):
    """Messages with a malformed LD signature block are rejected."""
    actor_uri = "https://remote.test/test-actor/"
    Domain.objects.create(domain="remote.test", local=False, state="updated")
    Identity.objects.create(
        actor_uri=actor_uri,
        domain=Domain.objects.get(domain="remote.test"),
        username="test",
        local=False,
        state="updated",
    )

    document = _make_document(actor_uri=actor_uri)
    # signature block missing "creator" key
    document["signature"] = {
        "created": "2023-10-25T08:08:47.702Z",
        "signatureValue": "something",
        "type": "RsaSignature2017",
    }

    resp = _post_to_inbox(client, identity, document)
    assert resp.status_code == 400


@pytest.mark.django_db
def test_ld_signature_creator_actor_mismatch_rejected(
    client, identity, remote_identity, keypair
):
    """LD signature with creator != actor is rejected (actor spoofing)."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    # Document claims to be from a different actor than the signer
    victim_uri = "https://victim.test/users/alice"
    Domain.objects.create(domain="victim.test", local=False, state="updated")
    document = _make_document(actor_uri=victim_uri)
    # Sign with remote_identity's key but creator doesn't match actor
    signature_section = LDSignature.create_signature(
        document,
        keypair["private_key"],
        f"{remote_identity.actor_uri}#main-key",
    )
    document["signature"] = signature_section

    resp = _post_to_inbox(client, identity, document)
    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_deferred_ld_sig_creator_actor_mismatch_rejected(keypair):
    """
    Deferred LD sig verification fails when creator doesn't match actor.
    """
    domain = Domain.objects.create(domain="deferred.test", local=False, state="updated")
    Identity.objects.create(
        actor_uri="https://deferred.test/actor/",
        domain=domain,
        username="deferred",
        local=False,
        state="updated",
        public_key=keypair["public_key"],
    )

    # Message actor differs from the LD sig creator
    document = _make_document(actor_uri="https://victim.test/users/alice")
    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "ld_sig": {
                "creator_uri": "https://deferred.test/actor/",
            }
        },
    )

    result = InboxMessageStates._verify_deferred(msg)
    assert result is False


@pytest.mark.django_db
def test_internal_type_rejected(client, identity, remote_identity, keypair):
    """Internal message types are rejected even with valid signatures."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    document["type"] = "__internal__"
    resp = _sign_and_post(client, identity, document, keypair)
    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_deferred_http_sig_verification_succeeds(keypair):
    """
    Deferred HTTP signature verification succeeds when the actor's
    public key becomes available.
    """
    # Create a remote identity without a public key initially
    domain = Domain.objects.create(domain="deferred.test", local=False, state="updated")
    remote = Identity.objects.create(
        actor_uri="https://deferred.test/actor/",
        domain=domain,
        username="deferred",
        local=False,
        state="updated",
    )

    # Build a legitimate signature
    cleartext = "(request-target): post /inbox/\nhost: example.com"
    private_key = serialization.load_pem_private_key(
        keypair["private_key"].encode(), password=None
    )
    sig_bytes = private_key.sign(
        cleartext.encode(), padding.PKCS1v15(), hashes.SHA256()
    )

    document = _make_document(actor_uri="https://deferred.test/actor/")
    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "http_sig": {
                "actor_uri": "https://deferred.test/actor/",
                "signature": base64.b64encode(sig_bytes).decode(),
                "headers_string": cleartext,
            }
        },
    )

    # Without key, verification returns None (retry)
    result = InboxMessageStates._verify_deferred(msg)
    assert result is None

    # Now give the identity a public key
    remote.public_key = keypair["public_key"]
    remote.save()

    # Verification should now succeed
    result = InboxMessageStates._verify_deferred(msg)
    assert result is True


@pytest.mark.django_db
def test_deferred_http_sig_verification_fails(keypair):
    """
    Deferred HTTP signature verification fails when the signature
    doesn't match the key.
    """
    domain = Domain.objects.create(domain="deferred.test", local=False, state="updated")
    Identity.objects.create(
        actor_uri="https://deferred.test/actor/",
        domain=domain,
        username="deferred",
        local=False,
        state="updated",
        public_key=keypair["public_key"],
    )

    document = _make_document(actor_uri="https://deferred.test/actor/")
    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "http_sig": {
                "actor_uri": "https://deferred.test/actor/",
                "signature": base64.b64encode(b"bad_signature").decode(),
                "headers_string": "fake cleartext",
            }
        },
    )

    result = InboxMessageStates._verify_deferred(msg)
    assert result is False


@pytest.mark.django_db
def test_deferred_ld_sig_verification_succeeds(keypair):
    """
    Deferred LD signature verification succeeds when the creator's
    public key becomes available.
    """
    domain = Domain.objects.create(domain="deferred.test", local=False, state="updated")
    remote = Identity.objects.create(
        actor_uri="https://deferred.test/actor/",
        domain=domain,
        username="deferred",
        local=False,
        state="updated",
    )

    document = _make_document(actor_uri="https://deferred.test/actor/")
    signature_section = LDSignature.create_signature(
        document,
        keypair["private_key"],
        keypair["public_key_id"],
    )
    document["signature"] = signature_section

    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "ld_sig": {
                "creator_uri": "https://deferred.test/actor/",
            }
        },
    )

    # Without key, verification returns None (retry)
    result = InboxMessageStates._verify_deferred(msg)
    assert result is None

    # Give the identity a public key
    remote.public_key = keypair["public_key"]
    remote.save()

    result = InboxMessageStates._verify_deferred(msg)
    assert result is True


@pytest.mark.django_db
def test_deferred_ld_sig_verification_fails(keypair):
    """
    Deferred LD signature verification fails with invalid signature.
    """
    domain = Domain.objects.create(domain="deferred.test", local=False, state="updated")
    Identity.objects.create(
        actor_uri="https://deferred.test/actor/",
        domain=domain,
        username="deferred",
        local=False,
        state="updated",
        public_key=keypair["public_key"],
    )

    document = _make_document(actor_uri="https://deferred.test/actor/")
    document["signature"] = {
        "creator": "https://deferred.test/actor/#main-key",
        "created": "2023-10-25T08:08:47.702Z",
        "signatureValue": base64.b64encode(b"invalid_signature").decode(),
        "type": "RsaSignature2017",
    }

    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "ld_sig": {
                "creator_uri": "https://deferred.test/actor/",
            }
        },
    )

    result = InboxMessageStates._verify_deferred(msg)
    assert result is False


@pytest.mark.django_db
def test_handle_received_deferred_blocks_processing(keypair):
    """
    handle_received returns errored for a deferred message whose
    signature fails verification, preventing message processing.
    """
    domain = Domain.objects.create(domain="deferred.test", local=False, state="updated")
    Identity.objects.create(
        actor_uri="https://deferred.test/actor/",
        domain=domain,
        username="deferred",
        local=False,
        state="updated",
        public_key=keypair["public_key"],
    )

    document = _make_document(actor_uri="https://deferred.test/actor/")
    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "http_sig": {
                "actor_uri": "https://deferred.test/actor/",
                "signature": base64.b64encode(b"forged").decode(),
                "headers_string": "forged cleartext",
            }
        },
    )

    result = InboxMessageStates.handle_received(msg)
    assert result == InboxMessageStates.errored


@pytest.mark.django_db
def test_handle_received_no_metadata_processes_normally(identity):
    """
    handle_received processes messages normally when no metadata
    is set (already verified at inbox time).
    """
    document = {
        "type": "Create",
        "actor": identity.actor_uri,
        "object": {
            "type": "Note",
            "content": "Test",
            "attributedTo": identity.actor_uri,
            "id": f"{identity.actor_uri}posts/test",
        },
        "id": f"{identity.actor_uri}activities/create/test",
    }
    msg = InboxMessage.objects.create(message=document)
    # Should attempt normal processing (may error on missing data, but
    # should not short-circuit on verification)
    result = InboxMessageStates.handle_received(msg)
    # The message processing itself may succeed or error depending on
    # downstream handlers, but it should not return None (retry)
    assert result is not None


# ---------------------------------------------------------------------------
# Relay fixtures and helpers
# ---------------------------------------------------------------------------

RELAY_ACTOR_URI = "https://relay.test/actor"
RELAY_KEY_ID = f"{RELAY_ACTOR_URI}#main-key"


@pytest.fixture
def relay_keypair():
    """Generate a fresh RSA keypair for the relay actor."""
    from core.signatures import RsaKeys

    private_key, public_key = RsaKeys.generate_keypair()
    return {
        "private_key": private_key,
        "public_key": public_key,
        "public_key_id": RELAY_KEY_ID,
    }


@pytest.fixture
@pytest.mark.django_db
def relay_identity(relay_keypair) -> Identity:
    """Remote relay actor with its own keypair."""
    domain = Domain.objects.create(domain="relay.test", local=False, state="updated")
    return Identity.objects.create(
        actor_uri=RELAY_ACTOR_URI,
        username="actor",
        domain=domain,
        name="Test Relay",
        local=False,
        state="updated",
        public_key=relay_keypair["public_key"],
        public_key_id=RELAY_KEY_ID,
    )


def _relay_sign_and_post(
    client,
    inbox_identity,
    document,
    relay_keypair,
    actor_keypair=None,
):
    """
    Post a document to the inbox HTTP-signed by a relay actor.

    relay_keypair is used for the HTTP signature (keyId = relay actor URI).
    If actor_keypair is provided an LD signature is attached using that keypair.
    """
    if actor_keypair:
        # Creator must match document["actor"] after urldefrag, so derive the
        # key ID from the document actor URI (preserve trailing slash).
        doc_actor_uri = document.get("actor", "")
        ld_key_id = (
            doc_actor_uri + "#main-key"
            if doc_actor_uri
            else actor_keypair["public_key_id"]
        )
        sig_section = LDSignature.create_signature(
            document,
            actor_keypair["private_key"],
            ld_key_id,
        )
        document = dict(document, signature=sig_section)

    body = json.dumps(document).encode()
    path = inbox_identity.inbox_uri.replace("https://example.com", "")
    digest = HttpSignature.calculate_digest(body)
    date_str = format_datetime(timezone.now(), usegmt=True)

    headers_to_sign = ["(request-target)", "host", "date", "digest", "content-type"]
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

    relay_private_key = serialization.load_pem_private_key(
        relay_keypair["private_key"].encode(), password=None
    )
    sig_bytes = relay_private_key.sign(
        headers_string.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    sig_header = (
        f'keyId="{RELAY_KEY_ID}",'
        f'headers="{" ".join(headers_to_sign)}",'
        f'signature="{base64.b64encode(sig_bytes).decode()}",'
        f'algorithm="rsa-sha256"'
    )

    return client.post(
        inbox_identity.inbox_uri,
        data=body,
        content_type="application/activity+json",
        HTTP_HOST="example.com",
        HTTP_DATE=date_str,
        HTTP_DIGEST=digest,
        HTTP_SIGNATURE=sig_header,
    )


# ---------------------------------------------------------------------------
# Relay tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_relay_valid_sigs_accepted(
    client, identity, remote_identity, relay_identity, relay_keypair, keypair
):
    """Relay HTTP sig + valid LD sig from document actor → 202."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    resp = _relay_sign_and_post(
        client,
        identity,
        document,
        relay_keypair,
        actor_keypair=keypair,
    )
    assert resp.status_code == 202
    msg = InboxMessage.objects.last()
    assert msg is not None
    assert msg.metadata is None


@pytest.mark.django_db
def test_relay_invalid_http_sig_rejected(
    client, identity, remote_identity, relay_identity, relay_keypair, keypair
):
    """Bad relay HTTP signature → 401."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    sig_section = LDSignature.create_signature(
        document, keypair["private_key"], keypair["public_key_id"]
    )
    document["signature"] = sig_section

    body = json.dumps(document).encode()
    digest = HttpSignature.calculate_digest(body)
    date_str = format_datetime(timezone.now(), usegmt=True)

    sig_header = (
        f'keyId="{RELAY_KEY_ID}",'
        f'headers="(request-target) host date digest content-type",'
        f'signature="{base64.b64encode(b"bad_relay_sig").decode()}",'
        f'algorithm="rsa-sha256"'
    )
    resp = client.post(
        identity.inbox_uri,
        data=body,
        content_type="application/activity+json",
        HTTP_HOST="example.com",
        HTTP_DATE=date_str,
        HTTP_DIGEST=digest,
        HTTP_SIGNATURE=sig_header,
    )
    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_relay_invalid_ld_sig_rejected(
    client, identity, remote_identity, relay_identity, relay_keypair, keypair
):
    """Valid relay HTTP sig but invalid LD sig → 401."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    document["signature"] = {
        "type": "RsaSignature2017",
        "creator": f"{remote_identity.actor_uri}#main-key",
        "created": "2023-10-25T08:08:47.702Z",
        "signatureValue": base64.b64encode(b"bad_ld_sig").decode(),
    }
    resp = _relay_sign_and_post(client, identity, document, relay_keypair)
    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_relay_missing_ld_sig_rejected(
    client, identity, remote_identity, relay_identity, relay_keypair, keypair
):
    """Valid relay HTTP sig but no LD sig → 401 (relay requires LD sig)."""
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    resp = _relay_sign_and_post(client, identity, document, relay_keypair)
    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0


@pytest.mark.django_db
def test_relay_unknown_relay_deferred(
    client, identity, remote_identity, relay_keypair, keypair
):
    """
    Relay actor with no cached key → message deferred with relay_http_sig metadata.
    """
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    # Relay domain exists but actor has no public key yet
    Domain.objects.create(domain="relay.test", local=False, state="updated")
    Identity.objects.create(
        actor_uri=RELAY_ACTOR_URI,
        username="actor",
        domain=Domain.objects.get(domain="relay.test"),
        name="Unknown Relay",
        local=False,
        state="updated",
    )

    document = _make_document(actor_uri=remote_identity.actor_uri)
    resp = _relay_sign_and_post(
        client,
        identity,
        document,
        relay_keypair,
        actor_keypair=keypair,
    )
    assert resp.status_code == 202
    msg = InboxMessage.objects.last()
    assert msg is not None
    assert msg.metadata is not None
    assert "relay_http_sig" in msg.metadata
    assert msg.metadata["relay_http_sig"]["relay_uri"] == RELAY_ACTOR_URI


@pytest.mark.django_db
def test_deferred_relay_http_sig_verified(relay_keypair):
    """
    Deferred relay_http_sig verifies once the relay's public key becomes available.
    LD sig was verified at inbox time so no ld_sig entry in metadata.
    """
    relay_domain = Domain.objects.create(
        domain="relay.test", local=False, state="updated"
    )
    relay = Identity.objects.create(
        actor_uri=RELAY_ACTOR_URI,
        username="actor",
        domain=relay_domain,
        name="Test Relay",
        local=False,
        state="updated",
    )

    cleartext = "(request-target): post /inbox/\nhost: example.com"
    relay_private_key = serialization.load_pem_private_key(
        relay_keypair["private_key"].encode(), password=None
    )
    sig_bytes = relay_private_key.sign(
        cleartext.encode(), padding.PKCS1v15(), hashes.SHA256()
    )

    document = _make_document()
    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "relay_http_sig": {
                "relay_uri": RELAY_ACTOR_URI,
                "signature": base64.b64encode(sig_bytes).decode(),
                "headers_string": cleartext,
            }
        },
    )

    # Without key, verification should retry (None)
    result = InboxMessageStates._verify_deferred(msg)
    assert result is None

    # Give the relay its public key
    relay.public_key = relay_keypair["public_key"]
    relay.save()

    msg.refresh_from_db()
    result = InboxMessageStates._verify_deferred(msg)
    assert result is True


@pytest.mark.django_db
def test_deferred_relay_http_sig_failed(relay_keypair):
    """Deferred relay_http_sig with wrong signature fails verification."""
    relay_domain = Domain.objects.create(
        domain="relay.test", local=False, state="updated"
    )
    Identity.objects.create(
        actor_uri=RELAY_ACTOR_URI,
        username="actor",
        domain=relay_domain,
        name="Test Relay",
        local=False,
        state="updated",
        public_key=relay_keypair["public_key"],
    )

    document = _make_document()
    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "relay_http_sig": {
                "relay_uri": RELAY_ACTOR_URI,
                "signature": base64.b64encode(b"bad").decode(),
                "headers_string": "fake cleartext",
            }
        },
    )

    result = InboxMessageStates._verify_deferred(msg)
    assert result is False


@pytest.mark.django_db
def test_deferred_both_relay_and_ld_sig_verified(relay_keypair, keypair):
    """
    Both relay_http_sig and ld_sig deferred: _verify_deferred verifies relay HTTP
    first, then ld_sig, and returns True only when both pass in sequence.
    """
    relay_domain = Domain.objects.create(
        domain="relay.test", local=False, state="updated"
    )
    relay = Identity.objects.create(
        actor_uri=RELAY_ACTOR_URI,
        username="actor",
        domain=relay_domain,
        name="Test Relay",
        local=False,
        state="updated",
    )
    doc_domain = Domain.objects.create(
        domain="author.test", local=False, state="updated"
    )
    author_uri = "https://author.test/users/alice"
    author = Identity.objects.create(
        actor_uri=author_uri,
        username="alice",
        domain=doc_domain,
        name="Alice",
        local=False,
        state="updated",
    )

    relay_cleartext = "(request-target): post /inbox/\nhost: example.com"
    relay_pk = serialization.load_pem_private_key(
        relay_keypair["private_key"].encode(), password=None
    )
    relay_sig_bytes = relay_pk.sign(
        relay_cleartext.encode(), padding.PKCS1v15(), hashes.SHA256()
    )

    document = _make_document(actor_uri=author_uri)
    ld_sig = LDSignature.create_signature(
        document, keypair["private_key"], keypair["public_key_id"]
    )
    document["signature"] = ld_sig

    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "relay_http_sig": {
                "relay_uri": RELAY_ACTOR_URI,
                "signature": base64.b64encode(relay_sig_bytes).decode(),
                "headers_string": relay_cleartext,
            },
            "ld_sig": {"creator_uri": author_uri, "raw_document": document},
        },
    )

    # Neither key available yet
    assert InboxMessageStates._verify_deferred(msg) is None

    # Relay key available; ld_sig still pending
    relay.public_key = relay_keypair["public_key"]
    relay.save()
    msg.refresh_from_db()
    assert InboxMessageStates._verify_deferred(msg) is None

    # Both keys now available → fully verified
    author.public_key = keypair["public_key"]
    author.save()
    msg.refresh_from_db()
    assert InboxMessageStates._verify_deferred(msg) is True


@pytest.mark.django_db
def test_deferred_relay_http_ok_ld_fails(relay_keypair, keypair):
    """
    relay_http_sig verifies but ld_sig is cryptographically invalid → False.
    """
    relay_domain = Domain.objects.create(
        domain="relay.test", local=False, state="updated"
    )
    Identity.objects.create(
        actor_uri=RELAY_ACTOR_URI,
        username="actor",
        domain=relay_domain,
        name="Test Relay",
        local=False,
        state="updated",
        public_key=relay_keypair["public_key"],
    )
    doc_domain = Domain.objects.create(
        domain="author.test", local=False, state="updated"
    )
    author_uri = "https://author.test/users/alice"
    Identity.objects.create(
        actor_uri=author_uri,
        username="alice",
        domain=doc_domain,
        name="Alice",
        local=False,
        state="updated",
        public_key=keypair["public_key"],
    )

    relay_cleartext = "(request-target): post /inbox/\nhost: example.com"
    relay_pk = serialization.load_pem_private_key(
        relay_keypair["private_key"].encode(), password=None
    )
    relay_sig_bytes = relay_pk.sign(
        relay_cleartext.encode(), padding.PKCS1v15(), hashes.SHA256()
    )

    document = _make_document(actor_uri=author_uri)
    document["signature"] = {
        "type": "RsaSignature2017",
        "creator": f"{author_uri}#main-key",
        "created": "2023-10-25T08:08:47.702Z",
        "signatureValue": base64.b64encode(b"invalid").decode(),
    }

    msg = InboxMessage.objects.create(
        message=document,
        metadata={
            "relay_http_sig": {
                "relay_uri": RELAY_ACTOR_URI,
                "signature": base64.b64encode(relay_sig_bytes).decode(),
                "headers_string": relay_cleartext,
            },
            "ld_sig": {"creator_uri": author_uri, "raw_document": document},
        },
    )

    assert InboxMessageStates._verify_deferred(msg) is False
