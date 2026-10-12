import base64
from unittest.mock import Mock, patch

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from takahe.auth import _parse_signature_header
from takahe.models import RsaKeys
from takahe.utils import Takahe


@pytest.mark.parametrize(
    "status, data, expected",
    [
        (
            200,
            {"alsoKnownAs": ["https://example.org/@alice"]},
            ["https://example.org/@alice"],
        ),
        (200, {}, None),
        (401, {}, ["https://example.org/@old"]),
    ],
)
def test_refresh_remote_identity_signs_get(httpx_mock, status, data, expected):
    """A signature-requiring peer can refresh migration aliases (82X)."""
    identity = Mock(
        pk=123,
        local=False,
        actor_uri="https://remote.example/users/alice",
        aliases=["https://example.org/@old"],
    )
    private_pem, public_pem = RsaKeys.generate_keypair()
    public_key = serialization.load_pem_public_key(public_pem.encode("ascii"))
    assert isinstance(public_key, rsa.RSAPublicKey)
    key_id = "https://example.org/actor/#main-key"

    def remote_actor(request):
        signature = _parse_signature_header(request.headers["Signature"])
        assert signature["keyId"] == key_id
        assert request.headers["Accept"] == "application/activity+json"
        cleartext = (
            f"(request-target): get /users/alice\n"
            f"host: remote.example\ndate: {request.headers['Date']}"
        ).encode()
        public_key.verify(
            base64.b64decode(signature["signature"]),
            cleartext,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return httpx.Response(status, json=data)

    httpx_mock.add_callback(remote_actor, url=identity.actor_uri, method="GET")
    with (
        patch("takahe.utils.Identity.objects.get", return_value=identity),
        patch("common.validators._hostname_is_public", return_value=True),
        patch(
            "takahe.auth._system_actor_signing_keys", return_value=(key_id, private_pem)
        ),
    ):
        Takahe.refresh_remote_identity(identity.pk)

    assert identity.aliases == expected
    if status == 200:
        identity.save.assert_called_once_with(update_fields=["aliases"])
    else:
        identity.save.assert_not_called()
