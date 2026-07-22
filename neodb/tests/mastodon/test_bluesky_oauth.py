import base64
import hashlib
import json
import time
from importlib import import_module
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from django.conf import settings
from django.test import RequestFactory
from django.urls import reverse

from common.models import SiteConfig
from mastodon.models import bluesky as bluesky_module
from mastodon.models import bluesky_oauth
from mastodon.models.bluesky import Bluesky, BlueskyAccount
from mastodon.models.bluesky_oauth import (
    SCOPE,
    DpopRequest,
    OAuthError,
    dpop_proof,
    generate_dpop_jwk,
    public_jwk,
    sign_jwt,
)


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _decode_segment(segment: str) -> dict:
    return json.loads(_b64url_decode(segment))


def _verify_es256(jwk: dict, token: str) -> tuple[dict, dict]:
    """Verify signature with plain cryptography, return (header, claims)."""
    header_b64, claims_b64, sig_b64 = token.split(".")
    signature = _b64url_decode(sig_b64)
    der = encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )
    public_key = ec.EllipticCurvePublicNumbers(
        int.from_bytes(_b64url_decode(jwk["x"]), "big"),
        int.from_bytes(_b64url_decode(jwk["y"]), "big"),
        ec.SECP256R1(),
    ).public_key()
    public_key.verify(
        der, f"{header_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256())
    )
    return _decode_segment(header_b64), _decode_segment(claims_b64)


def test_sign_jwt_roundtrip():
    jwk = generate_dpop_jwk()
    token = sign_jwt(jwk, {"alg": "ES256"}, {"iss": "me", "aud": "you"})
    header, claims = _verify_es256(jwk, token)
    assert header == {"alg": "ES256"}
    assert claims == {"iss": "me", "aud": "you"}


def test_public_jwk_has_no_private_part():
    jwk = generate_dpop_jwk()
    jwk["kid"] = "k1"
    pub = public_jwk(jwk)
    assert "d" not in pub
    assert pub["kid"] == "k1"
    assert pub["kty"] == "EC"


def test_dpop_proof_claims():
    jwk = generate_dpop_jwk()
    proof = dpop_proof(
        jwk,
        "get",
        "https://pds.example/xrpc/com.atproto.repo.getRecord?repo=x&rkey=y",
        nonce="n1",
        access_token="tok",
    )
    header, claims = _verify_es256(jwk, proof)
    assert header["typ"] == "dpop+jwt"
    assert header["alg"] == "ES256"
    assert "d" not in header["jwk"]
    assert claims["htm"] == "GET"
    # htu must not carry query or fragment
    assert claims["htu"] == "https://pds.example/xrpc/com.atproto.repo.getRecord"
    assert claims["nonce"] == "n1"
    assert claims["ath"] == (
        base64.urlsafe_b64encode(hashlib.sha256(b"tok").digest()).rstrip(b"=").decode()
    )


def test_authserver_post_retries_with_server_nonce(monkeypatch):
    jwk = generate_dpop_jwk()
    posts = []

    def fake_post(url, data=None, headers=None, timeout=None):
        posts.append((url, dict(data or {}), dict(headers or {})))
        if len(posts) == 1:
            return httpx.Response(
                400,
                json={"error": "use_dpop_nonce"},
                headers={"DPoP-Nonce": "server-nonce"},
            )
        return httpx.Response(
            200, json={"request_uri": "urn:x"}, headers={"DPoP-Nonce": "server-nonce"}
        )

    monkeypatch.setattr(bluesky_oauth.httpx, "post", fake_post)

    body, nonce = bluesky_oauth._authserver_post(
        "https://auth.example/par", {"a": "b"}, jwk, ""
    )

    assert body == {"request_uri": "urn:x"}
    assert nonce == "server-nonce"
    assert len(posts) == 2
    # second attempt carries the server-provided nonce in its proof
    _, claims = _verify_es256(jwk, posts[1][2]["DPoP"])
    assert claims["nonce"] == "server-nonce"
    _, first_claims = _verify_es256(jwk, posts[0][2]["DPoP"])
    assert "nonce" not in first_claims


def test_authserver_post_error_raises(monkeypatch):
    monkeypatch.setattr(
        bluesky_oauth.httpx,
        "post",
        lambda url, data=None, headers=None, timeout=None: httpx.Response(
            400, json={"error": "invalid_grant"}
        ),
    )
    with pytest.raises(OAuthError):
        bluesky_oauth._authserver_post(
            "https://auth.example/token", {}, generate_dpop_jwk(), ""
        )


@pytest.fixture
def _restore_site_config():
    # get_client_private_jwk() persists the generated key via
    # SiteConfig.reload(), which survives the test transaction rollback
    original = SiteConfig.system
    yield
    SiteConfig.system = original


@pytest.mark.django_db(databases="__all__")
def test_client_metadata_document(client, _restore_site_config):
    response = client.get(reverse("mastodon:bluesky_client_metadata"))
    assert response.status_code == 200
    meta = response.json()
    site_url = settings.SITE_INFO["site_url"]
    assert meta["client_id"] == site_url + reverse("mastodon:bluesky_client_metadata")
    assert meta["redirect_uris"] == [site_url + reverse("mastodon:bluesky_oauth")]
    assert meta["scope"] == SCOPE
    assert meta["dpop_bound_access_tokens"] is True
    assert meta["token_endpoint_auth_method"] == "private_key_jwt"
    keys = meta["jwks"]["keys"]
    assert len(keys) == 1
    assert keys[0]["kid"].startswith("neodb-")
    assert "d" not in keys[0]
    # the key is persisted, so a second read serves the same key
    response2 = client.get(reverse("mastodon:bluesky_client_metadata"))
    assert response2.json()["jwks"]["keys"][0] == keys[0]


class _StubHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, headers=None, **kwargs):
        self.requests.append((method, url, dict(headers or {})))
        return self.responses.pop(0)


class _FakeSession:
    def __init__(self):
        self.dpop_jwk = generate_dpop_jwk()
        self.pds_nonce = ""
        self.tokens = ["tok1"]
        self.refreshes = 0

    def get_dpop_jwk(self):
        return self.dpop_jwk

    def get_pds_nonce(self):
        return self.pds_nonce

    def save_pds_nonce(self, nonce):
        self.pds_nonce = nonce

    def get_access_token(self, force_refresh=False):
        if force_refresh:
            self.refreshes += 1
            self.tokens.append(f"tok{len(self.tokens) + 1}")
        return self.tokens[-1]


def test_dpop_request_signs_and_rotates_nonce():
    session = _FakeSession()
    request = DpopRequest(session)
    stub = _StubHttpClient(
        [
            httpx.Response(
                401,
                json={"error": "use_dpop_nonce"},
                headers={"DPoP-Nonce": "pds-nonce"},
            ),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    request._client = stub  # ty: ignore[invalid-assignment]  # stub transport

    response = request.get("https://pds.example/xrpc/app.bsky.actor.getProfile")

    assert response.success
    assert session.pds_nonce == "pds-nonce"
    assert len(stub.requests) == 2
    first_headers = stub.requests[0][2]
    assert first_headers["Authorization"] == "DPoP tok1"
    _, claims = _verify_es256(session.dpop_jwk, first_headers["DPoP"])
    assert claims["htm"] == "GET"
    assert claims["ath"]
    _, retry_claims = _verify_es256(session.dpop_jwk, stub.requests[1][2]["DPoP"])
    assert retry_claims["nonce"] == "pds-nonce"
    assert session.refreshes == 0


def test_dpop_request_refreshes_on_unexpected_401():
    session = _FakeSession()
    request = DpopRequest(session)
    stub = _StubHttpClient(
        [
            httpx.Response(401, json={"error": "invalid_token"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    request._client = stub  # ty: ignore[invalid-assignment]  # stub transport

    response = request.get("https://pds.example/xrpc/app.bsky.actor.getProfile")

    assert response.success
    assert session.refreshes == 1
    assert stub.requests[1][2]["Authorization"] == "DPoP tok2"


def _oauth_session(**over):
    return {
        "issuer": "https://auth.example",
        "token_endpoint": "https://auth.example/token",
        "dpop_jwk": generate_dpop_jwk(),
        "access_token": "old-token",
        "refresh_token": "refresh-1",
        "expires_at": int(time.time()) - 1,
        "scope": SCOPE,
        "authserver_nonce": "",
        "pds_nonce": "",
    } | over


def test_account_access_token_refreshes_when_expired(monkeypatch):
    account = BlueskyAccount(uid="did:plc:alice", domain="-")
    account.access_data = {}
    account._set_oauth(_oauth_session())
    calls = []

    def fake_refresh(token_endpoint, issuer, refresh_token, dpop_jwk, nonce):
        calls.append((token_endpoint, issuer, refresh_token))
        return (
            {
                "access_token": "new-token",
                "refresh_token": "refresh-2",
                "expires_in": 600,
            },
            "nonce-2",
        )

    monkeypatch.setattr(bluesky_module, "refresh_token_request", fake_refresh)

    assert account.get_access_token() == "new-token"

    assert calls == [
        ("https://auth.example/token", "https://auth.example", "refresh-1")
    ]
    stored = json.loads(account.oauth_session or "")
    assert stored["refresh_token"] == "refresh-2"
    assert stored["authserver_nonce"] == "nonce-2"
    assert stored["expires_at"] > time.time() + 500
    # fresh token is served without another refresh round-trip
    assert account.get_access_token() == "new-token"
    assert len(calls) == 1


def test_account_without_oauth_session_raises():
    account = BlueskyAccount(uid="did:plc:alice", domain="-")
    account.access_data = {}
    with pytest.raises(OAuthError):
        account.get_access_token()


def test_account_refresh_without_refresh_token_raises():
    account = BlueskyAccount(uid="did:plc:alice", domain="-")
    account.access_data = {}
    account._set_oauth(_oauth_session(refresh_token=""))
    with pytest.raises(OAuthError):
        account.get_access_token()


@pytest.mark.django_db(databases="__all__")
def test_generate_auth_url_pushes_request_and_saves_state(monkeypatch):
    monkeypatch.setattr(
        Bluesky,
        "_resolve_identity",
        lambda handle: ("did:plc:alice", "https://pds.example"),
    )
    monkeypatch.setattr(
        bluesky_module, "fetch_pds_authserver", lambda pds: "https://auth.example"
    )
    monkeypatch.setattr(
        bluesky_module,
        "fetch_authserver_metadata",
        lambda issuer: {
            "issuer": issuer,
            "authorization_endpoint": "https://auth.example/authorize",
            "token_endpoint": "https://auth.example/token",
            "pushed_authorization_request_endpoint": "https://auth.example/par",
        },
    )
    par_calls = []

    def fake_send_par(meta, dpop_jwk, *, handle, state, code_verifier):
        par_calls.append((meta["issuer"], handle, state, code_verifier))
        return "urn:ietf:params:oauth:request_uri:req-1", "par-nonce"

    monkeypatch.setattr(bluesky_module, "send_par", fake_send_par)

    request = RequestFactory().post("/account/bluesky/login")
    engine = import_module(settings.SESSION_ENGINE)
    request.session = engine.SessionStore()

    url = Bluesky.generate_auth_url("@Alice.Test", request)

    parts = urlsplit(url)
    assert url.startswith("https://auth.example/authorize?")
    query = parse_qs(parts.query)
    assert query["request_uri"] == ["urn:ietf:params:oauth:request_uri:req-1"]
    assert query["client_id"] == [bluesky_oauth.get_client_id()]
    pending = request.session["atproto_oauth"]
    assert pending["did"] == "did:plc:alice"
    assert pending["handle"] == "alice.test"  # normalized
    assert pending["issuer"] == "https://auth.example"
    assert pending["authserver_nonce"] == "par-nonce"
    assert pending["state"] == par_calls[0][2]
    assert pending["code_verifier"] == par_calls[0][3]
    assert "d" in pending["dpop_jwk"]


def test_generate_auth_url_rejects_invalid_handle():
    request = RequestFactory().post("/account/bluesky/login")
    with pytest.raises(OAuthError):
        Bluesky.generate_auth_url("not a handle", request)


@pytest.fixture
def bluesky_enabled(monkeypatch):
    enabled = SiteConfig.system.model_copy(update={"enable_login_bluesky": True})
    monkeypatch.setattr(SiteConfig, "system", enabled)
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)


def _prime_pending(client, **over):
    pending = {
        "state": "state-1",
        "did": "did:plc:alice",
        "handle": "alice.test",
        "pds_url": "https://pds.example",
        "issuer": "https://auth.example",
        "token_endpoint": "https://auth.example/token",
        "code_verifier": "v" * 43,
        "dpop_jwk": generate_dpop_jwk(),
        "authserver_nonce": "",
    } | over
    session = client.session
    session["atproto_oauth"] = pending
    session.save()
    return pending


@pytest.mark.django_db(databases="__all__")
def test_oauth_callback_registers_new_user(client, monkeypatch, bluesky_enabled):
    _prime_pending(client)
    monkeypatch.setattr(
        bluesky_module,
        "initial_token_request",
        lambda token_endpoint, issuer, code, verifier, jwk, nonce: (
            {
                "sub": "did:plc:alice",
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 300,
                "scope": SCOPE,
            },
            "nonce-1",
        ),
    )
    monkeypatch.setattr(
        BlueskyAccount, "refresh", lambda self, save=True, did_check=True: True
    )

    response = client.get(
        reverse("mastodon:bluesky_oauth"),
        {"code": "code-1", "state": "state-1", "iss": "https://auth.example"},
    )

    assert response.status_code == 302
    assert response.url == reverse("users:register")
    verified = client.session["verified_account"]
    assert verified["uid"] == "did:plc:alice"
    account = BlueskyAccount.from_dict(verified)
    stored = account._get_oauth()
    assert stored["access_token"] == "at-1"
    assert stored["refresh_token"] == "rt-1"
    assert stored["authserver_nonce"] == "nonce-1"
    assert not account.session_string


@pytest.mark.django_db(databases="__all__")
def test_oauth_callback_rejects_state_mismatch(client, bluesky_enabled):
    _prime_pending(client)
    response = client.get(
        reverse("mastodon:bluesky_oauth"), {"code": "c", "state": "wrong"}
    )
    assert response.status_code == 200
    assert b"Authentication failed" in response.content


@pytest.mark.django_db(databases="__all__")
def test_oauth_callback_rejects_sub_mismatch(client, monkeypatch, bluesky_enabled):
    _prime_pending(client)
    monkeypatch.setattr(
        bluesky_module,
        "initial_token_request",
        lambda *args: (
            {"sub": "did:plc:mallory", "access_token": "x", "scope": SCOPE},
            "",
        ),
    )
    response = client.get(
        reverse("mastodon:bluesky_oauth"), {"code": "c", "state": "state-1"}
    )
    assert response.status_code == 200
    assert b"Authentication failed" in response.content


@pytest.mark.django_db(databases="__all__")
def test_oauth_callback_rejects_iss_mismatch(client, bluesky_enabled):
    _prime_pending(client)
    response = client.get(
        reverse("mastodon:bluesky_oauth"),
        {"code": "c", "state": "state-1", "iss": "https://evil.example"},
    )
    assert response.status_code == 200
    assert b"Authentication failed" in response.content


@pytest.mark.django_db(databases="__all__")
def test_oauth_callback_shows_authserver_error(client, bluesky_enabled):
    _prime_pending(client)
    response = client.get(
        reverse("mastodon:bluesky_oauth"),
        {"error": "access_denied", "error_description": "user said no"},
    )
    assert response.status_code == 200
    assert b"user said no" in response.content
    assert "atproto_oauth" not in client.session


def test_authserver_metadata_rejects_http_endpoint(monkeypatch, settings):
    settings.DEBUG = False
    meta = {
        "issuer": "https://auth.example",
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "http://169.254.169.254/token",
        "pushed_authorization_request_endpoint": "https://auth.example/par",
    }
    monkeypatch.setattr(
        bluesky_oauth.httpx,
        "get",
        lambda url, timeout=None: httpx.Response(
            200, json=meta, request=httpx.Request("GET", url)
        ),
    )
    with pytest.raises(OAuthError, match="token_endpoint"):
        bluesky_oauth.fetch_authserver_metadata("https://auth.example")


def test_account_force_refresh_rotates_unexpired_token(monkeypatch):
    # the PDS may reject a token before its recorded expiry; a forced
    # refresh must not trust expires_at and return the same token
    account = BlueskyAccount(uid="did:plc:alice", domain="-")
    account.access_data = {}
    account._set_oauth(_oauth_session(expires_at=int(time.time()) + 3600))
    monkeypatch.setattr(
        bluesky_module,
        "refresh_token_request",
        lambda *args: (
            {"access_token": "rotated", "refresh_token": "r2", "expires_in": 600},
            "n2",
        ),
    )

    assert account.get_access_token() == "old-token"
    assert account.get_access_token(force_refresh=True) == "rotated"


@pytest.mark.django_db(databases="__all__")
def test_oauth_callback_persists_tokens_even_if_refresh_fails(
    client, monkeypatch, bluesky_enabled
):
    from users.models import User

    user = User.register(email="cb@example.com", username="cbuser")
    account = BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:alice", handle="alice.test"
    )
    account._set_oauth(_oauth_session(access_token="stale"), save=True)
    _prime_pending(client)
    monkeypatch.setattr(
        bluesky_module,
        "initial_token_request",
        lambda *args: (
            {
                "sub": "did:plc:alice",
                "access_token": "fresh",
                "refresh_token": "rt-2",
                "expires_in": 300,
                "scope": SCOPE,
            },
            "n1",
        ),
    )
    # simulate a transient PDS failure right after the token exchange
    monkeypatch.setattr(
        BlueskyAccount, "refresh", lambda self, save=True, did_check=True: False
    )

    response = client.get(
        reverse("mastodon:bluesky_oauth"), {"code": "c", "state": "state-1"}
    )

    assert response.status_code == 302
    stored = BlueskyAccount.objects.get(pk=account.pk)._get_oauth()
    assert stored["access_token"] == "fresh"
    assert stored["refresh_token"] == "rt-2"
