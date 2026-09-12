import base64
import hashlib
import json
import time
from unittest.mock import Mock

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import RequestFactory

from users.models import User, WebAuthnCredential
from users.views import webauthn

CHALLENGE = b"test-passkey-challenge"
CREDENTIAL_ID = b"test-credential"
RP_ID = "example.org"
ORIGIN = "https://example.org"


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def request_for(body, ceremony, user=None):
    request = RequestFactory().post("/passkey/", body, content_type="application/json")
    request.user = user or User(username="passkey-user")
    request.session = SessionStore()
    request.session[f"webauthn_{ceremony}_challenge"] = {
        "challenge": base64.b64encode(CHALLENGE).decode(),
        "ts": time.time(),
    }
    return request


@pytest.fixture
def key():
    private_key = ec.generate_private_key(ec.SECP256R1())
    numbers = private_key.public_key().public_numbers()
    public_key = cbor2.dumps(
        {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32), -3: numbers.y.to_bytes(32)}
    )
    return private_key, public_key


def credential_response(key, ceremony, verified):
    private_key, public_key = key
    client_data = json.dumps(
        {
            "type": "webauthn.create" if ceremony == "register" else "webauthn.get",
            "challenge": b64(CHALLENGE),
            "origin": ORIGIN,
        }
    ).encode()
    flags = 0x01 | (0x04 if verified else 0) | (0x40 if ceremony == "register" else 0)
    auth_data = (
        hashlib.sha256(RP_ID.encode()).digest() + bytes([flags]) + (1).to_bytes(4)
    )
    response = {"clientDataJSON": b64(client_data)}
    if ceremony == "register":
        auth_data += (
            bytes(16) + len(CREDENTIAL_ID).to_bytes(2) + CREDENTIAL_ID + public_key
        )
        response["attestationObject"] = b64(
            cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        )
    else:
        response["authenticatorData"] = b64(auth_data)
        response["signature"] = b64(
            private_key.sign(
                auth_data + hashlib.sha256(client_data).digest(),
                ec.ECDSA(hashes.SHA256()),
            )
        )
    return {
        "id": b64(CREDENTIAL_ID),
        "rawId": b64(CREDENTIAL_ID),
        "type": "public-key",
        "response": response,
    }


@pytest.mark.django_db(databases="__all__")
@pytest.mark.parametrize("ceremony", ["register", "login"])
@pytest.mark.parametrize("verified", [False, True])
def test_passkey_requires_user_verification(
    key, ceremony, verified, settings, monkeypatch
):
    settings.WEBAUTHN_RP_ID = RP_ID
    settings.WEBAUTHN_ORIGIN = ORIGIN
    user = User.objects.create(username="passkey-user")
    login = Mock()
    monkeypatch.setattr(webauthn, "auth_login", login)
    if ceremony == "login":
        WebAuthnCredential.objects.create(
            user=user, credential_id=CREDENTIAL_ID, public_key=key[1]
        )
    request = request_for(credential_response(key, ceremony, verified), ceremony, user)
    response = getattr(webauthn, f"passkey_{ceremony}_verify")(request)
    assert response.status_code == (200 if verified else 400)
    if ceremony == "register":
        assert WebAuthnCredential.objects.exists() is verified
    else:
        assert login.called is verified
        credential = WebAuthnCredential.objects.get()
        assert credential.sign_count == (1 if verified else 0)
        assert (credential.last_used is not None) is verified


@pytest.mark.parametrize("body", ["null", "[]", '"text"', "42"])
@pytest.mark.parametrize(
    "action", ["register_verify", "login_verify", "delete", "rename"]
)
def test_passkey_rejects_non_object_json(body, action):
    ceremony = action.split("_")[0]
    request = request_for(body, ceremony)
    response = getattr(webauthn, f"passkey_{action}")(request)
    assert response.status_code == 400


@pytest.mark.parametrize("name", [None, 123, [], {}])
def test_rename_rejects_non_string_name(name):
    request = request_for({"id": 1, "name": name}, "rename")
    response = webauthn.passkey_rename(request)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "body", [{"name": None}, {"name": 123}, {"transports": None}, {"transports": "usb"}]
)
def test_register_rejects_invalid_metadata(body, monkeypatch):
    verify = Mock()
    monkeypatch.setattr(webauthn, "verify_registration_response", verify)
    response = webauthn.passkey_register_verify(request_for(body, "register"))
    assert response.status_code == 400
    verify.assert_not_called()
