import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from users.managed_identity import (
    ManagedIdentityConflictError,
    bind_managed_identity,
    login_managed_identity,
    logout_product_session,
    resolve_managed_identity,
)
from users.models import ManagedIdentityBinding, User
from users.oneid import (
    OneIDClient,
    OneIDConfig,
    OneIDProviderError,
    OneIDValidationError,
    VerifiedManagedIdentity,
)

ISSUER = "https://oneid.example.test/tenant"
CLIENT_ID = "vinylhub-test-client"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = f"{ISSUER}/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/token"
JWKS_URI = f"{ISSUER}/jwks"
REDIRECT_URI = "https://vinylhub.example.test/account/oneid/callback"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _json_b64(value: dict) -> str:
    return _b64(json.dumps(value, separators=(",", ":")).encode())


def _jwk(key: rsa.RSAPrivateKey, kid: str = "test-key") -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _id_token(key: rsa.RSAPrivateKey, claims: dict, kid: str = "test-key") -> str:
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    signing_input = f"{_json_b64(header)}.{_json_b64(claims)}"
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


def _response(method: str, url: str, status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request(method, url),
    )


def _config() -> OneIDConfig:
    return OneIDConfig(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="",
        discovery_url=DISCOVERY_URL,
        redirect_uri=REDIRECT_URI,
        scope="openid",
        subject_claim="sub",
        accepted_source_attributes=("phone_number", "email", "nickname"),
        clock_skew=0,
        timeout=2,
    )


def _metadata() -> dict:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHORIZATION_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
        "jwks_uri": JWKS_URI,
    }


_SESSION_KEY = "oneid_oidc"


def _claims(pending, **updates):
    result = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 300,
        "nbf": int(time.time()) - 10,
        "nonce": pending["nonce"],
        "sub": "subject-123",
        "phone_number": "+8613800000000",
        "email": "mutable@example.test",
        "nickname": "mutable-name",
    }
    result.update(updates)
    return result


def test_authorization_code_pkce_and_valid_identity(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls = {"post": None}

    def fake_get(url, **kwargs):
        del kwargs
        if url == DISCOVERY_URL:
            return _response("GET", url, 200, _metadata())
        if url == JWKS_URI:
            return _response("GET", url, 200, {"keys": [_jwk(key)]})
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "get", fake_get)
    factory = RequestFactory()
    start_request = factory.get("/account/oneid/start")
    start_request.session = SessionStore()
    client = OneIDClient(_config())
    authorization_url = client.authorization_url(start_request)
    params = parse_qs(urlsplit(authorization_url).query)
    pending = start_request.session[_SESSION_KEY]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [
        _b64(hashlib.sha256(pending["code_verifier"].encode()).digest())
    ]

    id_token = _id_token(key, _claims(pending))

    def fake_post(url, data, **kwargs):
        del kwargs
        calls["post"] = data
        return _response("POST", url, 200, {"id_token": id_token})

    monkeypatch.setattr(httpx, "post", fake_post)
    callback_request = factory.get(
        "/account/oneid/callback",
        {"state": pending["state"], "code": "authorization-code"},
    )
    callback_request.session = start_request.session
    identity = client.verify_callback(callback_request)
    assert identity == VerifiedManagedIdentity(
        issuer=ISSUER,
        subject="subject-123",
        accepted_source_attributes={
            "phone_number": "+8613800000000",
            "email": "mutable@example.test",
            "nickname": "mutable-name",
        },
    )
    assert calls["post"]["code_verifier"] == pending["code_verifier"]
    assert "refresh_token" not in calls["post"]


@pytest.mark.parametrize(
    "updates",
    [
        {"iss": "https://other.example.test"},
        {"aud": "other-client"},
        {"exp": 1},
        {"nbf": int(time.time()) + 300},
        {"sub": ""},
    ],
    ids=[
        "wrong-issuer",
        "wrong-audience",
        "expired",
        "not-yet-valid",
        "missing-subject",
    ],
)
def test_invalid_claims_fail_closed(monkeypatch, updates):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls = {"post": 0}

    def fake_get(url, **kwargs):
        del kwargs
        if url == DISCOVERY_URL:
            return _response("GET", url, 200, _metadata())
        if url == JWKS_URI:
            return _response("GET", url, 200, {"keys": [_jwk(key)]})
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "get", fake_get)
    factory = RequestFactory()
    start_request = factory.get("/account/oneid/start")
    start_request.session = SessionStore()
    client = OneIDClient(_config())
    client.authorization_url(start_request)
    pending = start_request.session[_SESSION_KEY]

    def fake_post(url, data, **kwargs):
        del data, kwargs
        calls["post"] += 1
        return _response(
            "POST", url, 200, {"id_token": _id_token(key, _claims(pending, **updates))}
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    callback_request = factory.get(
        "/account/oneid/callback",
        {"state": pending["state"], "code": "authorization-code"},
    )
    callback_request.session = start_request.session
    with pytest.raises(OneIDValidationError):
        client.verify_callback(callback_request)
    assert calls["post"] == 1


def test_bad_signature_and_state_fail_closed(monkeypatch):
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def fake_get(url, **kwargs):
        del kwargs
        if url == DISCOVERY_URL:
            return _response("GET", url, 200, _metadata())
        if url == JWKS_URI:
            return _response("GET", url, 200, {"keys": [_jwk(wrong_key)]})
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "get", fake_get)
    factory = RequestFactory()
    start_request = factory.get("/account/oneid/start")
    start_request.session = SessionStore()
    client = OneIDClient(_config())
    client.authorization_url(start_request)
    pending = start_request.session[_SESSION_KEY]
    token = _id_token(signing_key, _claims(pending))
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: _response(
            "POST", TOKEN_ENDPOINT, 200, {"id_token": token}
        ),
    )
    bad_state = factory.get(
        "/account/oneid/callback", {"state": "wrong", "code": "code"}
    )
    bad_state.session = start_request.session
    with pytest.raises(OneIDValidationError):
        client.verify_callback(bad_state)

    # Re-create the one-shot state and prove a bad PKCE exchange is rejected
    # without producing a verified identity.
    start_request.session[_SESSION_KEY] = pending
    pending["code_verifier"] = "tampered-verifier"
    callback = factory.get(
        "/account/oneid/callback",
        {"state": pending["state"], "code": "code"},
    )
    callback.session = start_request.session
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: _response(
            "POST", TOKEN_ENDPOINT, 400, {"error": "invalid_grant"}
        ),
    )
    with pytest.raises(OneIDProviderError):
        client.verify_callback(callback)


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_binding_lookup_is_subject_stable_and_unbound_does_not_create_user():
    user = User.register(username="managed-existing")
    identity = VerifiedManagedIdentity(ISSUER, "stable-subject", {})
    assert User.objects.count() == 1
    unbound = resolve_managed_identity(identity)
    assert unbound.bootstrap_required
    assert unbound.user is None
    assert User.objects.count() == 1

    binding = bind_managed_identity(identity, user)
    resolved = resolve_managed_identity(identity)
    assert binding.pk == resolved.binding.pk
    assert resolved.user.pk == user.pk
    assert not resolved.bootstrap_required

    same_subject_other_issuer = VerifiedManagedIdentity(
        "https://other.example.test", "stable-subject", {}
    )
    assert resolve_managed_identity(same_subject_other_issuer).bootstrap_required

    mutable_attrs_only = VerifiedManagedIdentity(
        ISSUER,
        "another-subject",
        {"phone_number": "+8613800000000", "email": "mutable@example.test"},
    )
    assert resolve_managed_identity(mutable_attrs_only).bootstrap_required
    assert ManagedIdentityBinding.objects.count() == 1


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_existing_binding_cannot_be_reassigned():
    first = User.register(username="managed-first")
    second = User.register(username="managed-second")
    identity = VerifiedManagedIdentity(ISSUER, "owned-subject", {})
    bind_managed_identity(identity, first)
    with pytest.raises(ManagedIdentityConflictError):
        bind_managed_identity(identity, second)
    assert ManagedIdentityBinding.objects.get().user_id == first.pk


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_concurrent_binding_creation_converges_on_postgresql():
    user = User.register(username="managed-concurrent")
    identity = VerifiedManagedIdentity(ISSUER, "concurrent-subject", {})

    def create_binding(_):
        from django.db import close_old_connections

        close_old_connections()
        try:
            return bind_managed_identity(identity, user).pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(create_binding, range(2)))
    assert result[0] == result[1]
    assert (
        ManagedIdentityBinding.objects.filter(
            issuer=ISSUER, subject="concurrent-subject"
        ).count()
        == 1
    )


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_native_product_session_create_read_rotate_logout():
    user = User.register(username="managed-session")
    identity = VerifiedManagedIdentity(ISSUER, "session-subject", {})
    bind_managed_identity(identity, user)
    factory = RequestFactory()
    request = factory.get("/")
    request.session = SessionStore()
    request.session.save()
    old_session_key = request.session.session_key

    resolution = login_managed_identity(request, identity)
    assert resolution.user.pk == user.pk
    assert request.session.session_key != old_session_key
    assert get_user(request).pk == user.pk
    request.session.save()

    readback_request = factory.get("/")
    readback_request.session = SessionStore(request.session.session_key)
    assert get_user(readback_request).pk == user.pk

    logout_product_session(request)
    assert not get_user(request).is_authenticated
    post_logout_request = factory.get("/")
    post_logout_request.session = SessionStore(readback_request.session.session_key)
    assert not get_user(post_logout_request).is_authenticated
