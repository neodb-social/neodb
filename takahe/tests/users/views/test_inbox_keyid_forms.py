import base64
import json
from email.utils import format_datetime

import pytest
from core.signatures import HttpSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.utils import timezone

from tests.users.views.test_inbox_auth import _make_document
from users.models import InboxMessage


def _sign_and_post_with_key_id(client, identity, document, keypair, key_id):
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
    signature_b64 = base64.b64encode(
        private_key.sign(headers_string.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
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
@pytest.mark.parametrize("suffix", ["#main-key", "/#main-key", "/main-key"])
def test_actor_key_id_forms_are_direct_deliveries(
    client, identity, remote_identity, keypair, suffix
):
    # GoToSocial uses /main-key, BookWyrm adds a trailing slash before the
    # fragment; neither sends LD signatures, so relay mode would refuse them
    actor_uri = remote_identity.actor_uri.rstrip("/")
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = actor_uri + suffix
    remote_identity.save()

    document = _make_document(actor_uri=remote_identity.actor_uri)
    resp = _sign_and_post_with_key_id(
        client, identity, document, keypair, actor_uri + suffix
    )

    assert resp.status_code == 202
    assert InboxMessage.objects.count() == 1


@pytest.mark.django_db
def test_other_actor_key_id_is_still_a_relay(
    client, identity, remote_identity, keypair
):
    remote_identity.public_key = keypair["public_key"]
    remote_identity.public_key_id = keypair["public_key_id"]
    remote_identity.save()

    document = _make_document(actor_uri="https://remote.test/someone-else/")
    resp = _sign_and_post_with_key_id(
        client, identity, document, keypair, remote_identity.actor_uri + "#main-key"
    )

    assert resp.status_code == 401
    assert InboxMessage.objects.count() == 0
