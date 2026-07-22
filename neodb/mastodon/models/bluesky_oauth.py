"""
ATProto OAuth client, per https://atproto.com/specs/oauth

NeoDB acts as a confidential OAuth client: the client metadata document is
served under /account/bluesky/client-metadata.json (its public URL is the
client_id), token requests are authenticated with a private_key_jwt assertion
signed by an auto-generated ES256 key persisted in SiteConfig, and all access
tokens are DPoP-bound as the spec requires.
"""

import base64
import hashlib
import json
import secrets
import time
import typing
from urllib.parse import urlsplit

import httpx
from atproto_client.request import Request, _handle_request_errors, _handle_response
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from django.conf import settings
from django.db import transaction
from django.urls import reverse

from common.models import SiteConfig

SCOPE = "atproto transition:generic"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_TIMEOUT = 10


class OAuthError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _json_b64(obj: dict) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":")).encode())


def generate_dpop_jwk() -> dict[str, str]:
    """Generate an ES256 (P-256) private key as a JWK dict."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers()
    public = private.public_numbers
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url(public.x.to_bytes(32, "big")),
        "y": _b64url(public.y.to_bytes(32, "big")),
        "d": _b64url(private.private_value.to_bytes(32, "big")),
    }


def public_jwk(jwk: dict) -> dict:
    return {
        k: jwk[k] for k in ("kty", "crv", "x", "y", "kid", "alg", "use") if k in jwk
    }


def _private_key(jwk: dict) -> ec.EllipticCurvePrivateKey:
    public = ec.EllipticCurvePublicNumbers(
        int.from_bytes(_b64url_decode(jwk["x"]), "big"),
        int.from_bytes(_b64url_decode(jwk["y"]), "big"),
        ec.SECP256R1(),
    )
    private_value = int.from_bytes(_b64url_decode(jwk["d"]), "big")
    return ec.EllipticCurvePrivateNumbers(private_value, public).private_key()


def sign_jwt(private_jwk: dict, header: dict, claims: dict) -> str:
    signing_input = f"{_json_b64(header)}.{_json_b64(claims)}"
    der = _private_key(private_jwk).sign(
        signing_input.encode(), ec.ECDSA(hashes.SHA256())
    )
    r, s = decode_dss_signature(der)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{signing_input}.{_b64url(signature)}"


def get_client_private_jwk() -> dict:
    """The instance-wide ES256 client key, generated and persisted on first use."""
    SiteConfig.ensure_loaded()
    raw = SiteConfig.system.atproto_client_jwk
    if not raw:
        with transaction.atomic():
            obj, _ = SiteConfig.objects.select_for_update().get_or_create(
                pk=1, defaults={"data": {}}
            )
            raw = obj.data.get("atproto_client_jwk", "")
            if not raw:
                jwk = generate_dpop_jwk()
                jwk["kid"] = f"neodb-{secrets.token_hex(8)}"
                raw = json.dumps(jwk)
                obj.data = {**obj.data, "atproto_client_jwk": raw}
                obj.save(update_fields=["data"])
        SiteConfig.reload()
    return json.loads(raw)


def get_client_id() -> str:
    return settings.SITE_INFO["site_url"] + reverse("mastodon:bluesky_client_metadata")


def get_redirect_uri() -> str:
    return settings.SITE_INFO["site_url"] + reverse("mastodon:bluesky_oauth")


def get_client_metadata() -> dict:
    return {
        "client_id": get_client_id(),
        "client_name": settings.SITE_INFO["site_name"],
        "client_uri": settings.SITE_INFO["site_url"],
        "redirect_uris": [get_redirect_uri()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": SCOPE,
        "application_type": "web",
        "dpop_bound_access_tokens": True,
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_signing_alg": "ES256",
        "jwks": {"keys": [public_jwk(get_client_private_jwk())]},
    }


def _require_https(url: str, what: str) -> None:
    if urlsplit(url).scheme != "https" and not settings.DEBUG:
        raise OAuthError(f"{what} is not https: {url}")


def fetch_pds_authserver(pds_url: str) -> str:
    """Resolve the authorization server issuer for a PDS."""
    _require_https(pds_url, "PDS endpoint")
    url = f"{pds_url.rstrip('/')}/.well-known/oauth-protected-resource"
    try:
        r = httpx.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        servers = r.json().get("authorization_servers")
    except Exception as e:
        raise OAuthError(f"error fetching PDS resource metadata: {e}") from e
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
        raise OAuthError("PDS lists no authorization server")
    return servers[0]


def fetch_authserver_metadata(issuer: str) -> dict:
    _require_https(issuer, "authorization server")
    url = f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server"
    try:
        r = httpx.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        meta = r.json()
    except Exception as e:
        raise OAuthError(f"error fetching authorization server metadata: {e}") from e
    if meta.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
        raise OAuthError("issuer mismatch in authorization server metadata")
    for key in (
        "authorization_endpoint",
        "token_endpoint",
        "pushed_authorization_request_endpoint",
    ):
        if not meta.get(key):
            raise OAuthError(f"authorization server metadata missing {key}")
    return meta


def dpop_proof(
    dpop_jwk: dict, method: str, url: str, *, nonce: str = "", access_token: str = ""
) -> str:
    parts = urlsplit(url)
    now = int(time.time())
    claims: dict[str, typing.Any] = {
        "jti": secrets.token_urlsafe(16),
        "htm": method.upper(),
        "htu": f"{parts.scheme}://{parts.netloc}{parts.path}",
        "iat": now,
        "exp": now + 60,
    }
    if nonce:
        claims["nonce"] = nonce
    if access_token:
        claims["ath"] = _b64url(hashlib.sha256(access_token.encode()).digest())
    header = {
        "typ": "dpop+jwt",
        "alg": "ES256",
        "jwk": {k: dpop_jwk[k] for k in ("kty", "crv", "x", "y")},
    }
    return sign_jwt(dpop_jwk, header, claims)


def client_assertion(audience: str) -> str:
    jwk = get_client_private_jwk()
    client_id = get_client_id()
    now = int(time.time())
    return sign_jwt(
        jwk,
        {"alg": "ES256", "kid": jwk["kid"]},
        {
            "iss": client_id,
            "sub": client_id,
            "aud": audience,
            "jti": secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + 300,
        },
    )


def _authserver_post(
    url: str, data: dict, dpop_jwk: dict, nonce: str
) -> tuple[dict, str]:
    """POST to an auth server endpoint with DPoP, retrying once when the
    server demands a (new) nonce. Returns (json body, latest DPoP nonce)."""
    for _ in range(2):
        headers = {"DPoP": dpop_proof(dpop_jwk, "POST", url, nonce=nonce)}
        try:
            r = httpx.post(url, data=data, headers=headers, timeout=_TIMEOUT)
        except Exception as e:
            raise OAuthError(f"error requesting {url}: {e}") from e
        nonce = r.headers.get("DPoP-Nonce", nonce)
        if r.status_code in (400, 401):
            try:
                error = r.json().get("error")
            except Exception:
                error = None
            if error == "use_dpop_nonce":
                continue
        break
    if r.status_code not in (200, 201):
        raise OAuthError(f"{url} returned {r.status_code}: {r.text[:200]}")
    return r.json(), nonce


def send_par(
    authserver_meta: dict,
    dpop_jwk: dict,
    *,
    handle: str,
    state: str,
    code_verifier: str,
) -> tuple[str, str]:
    """Pushed Authorization Request; returns (request_uri, DPoP nonce)."""
    challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
    data = {
        "response_type": "code",
        "client_id": get_client_id(),
        "redirect_uri": get_redirect_uri(),
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": handle,
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": client_assertion(authserver_meta["issuer"]),
    }
    body, nonce = _authserver_post(
        authserver_meta["pushed_authorization_request_endpoint"], data, dpop_jwk, ""
    )
    request_uri = body.get("request_uri")
    if not request_uri:
        raise OAuthError("PAR response missing request_uri")
    return request_uri, nonce


def initial_token_request(
    token_endpoint: str,
    issuer: str,
    code: str,
    code_verifier: str,
    dpop_jwk: dict,
    nonce: str,
) -> tuple[dict, str]:
    data = {
        "grant_type": "authorization_code",
        "client_id": get_client_id(),
        "redirect_uri": get_redirect_uri(),
        "code": code,
        "code_verifier": code_verifier,
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": client_assertion(issuer),
    }
    return _authserver_post(token_endpoint, data, dpop_jwk, nonce)


def refresh_token_request(
    token_endpoint: str, issuer: str, refresh_token: str, dpop_jwk: dict, nonce: str
) -> tuple[dict, str]:
    data = {
        "grant_type": "refresh_token",
        "client_id": get_client_id(),
        "refresh_token": refresh_token,
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": client_assertion(issuer),
    }
    return _authserver_post(token_endpoint, data, dpop_jwk, nonce)


def _is_use_dpop_nonce(response: httpx.Response) -> bool:
    if "use_dpop_nonce" in response.headers.get("WWW-Authenticate", ""):
        return True
    try:
        return response.json().get("error") == "use_dpop_nonce"
    except Exception:
        return False


class OAuthSessionProvider(typing.Protocol):
    """What DpopRequest needs from an account holding an OAuth session."""

    def get_dpop_jwk(self) -> dict: ...

    def get_pds_nonce(self) -> str: ...

    def save_pds_nonce(self, nonce: str) -> None: ...

    def get_access_token(self, force_refresh: bool = False) -> str: ...


class DpopRequest(Request):
    """atproto SDK transport that signs every XRPC call with the account's
    DPoP key and OAuth access token, transparently handling server nonce
    rotation and expired-token refresh."""

    _MAX_ATTEMPTS = 4

    def __init__(self, session: OAuthSessionProvider | None = None) -> None:
        super().__init__()
        self._oauth = session

    def clone(self):
        # Request.clone() constructs type(self)() with no arguments
        cloned = super().clone()
        cloned._oauth = self._oauth
        return cloned

    def _send_request(self, method: str, url: str, **kwargs: typing.Any):
        if self._oauth is None:
            raise OAuthError("no OAuth session bound to this client")
        base_headers = self.get_headers(kwargs.pop("headers", None))
        refreshed = False
        for attempt in range(self._MAX_ATTEMPTS):
            token = self._oauth.get_access_token()
            headers = dict(base_headers)
            headers["Authorization"] = f"DPoP {token}"
            headers["DPoP"] = dpop_proof(
                self._oauth.get_dpop_jwk(),
                method,
                url,
                nonce=self._oauth.get_pds_nonce(),
                access_token=token,
            )
            try:
                response = self._client.request(
                    method=method, url=url, headers=headers, **kwargs
                )
            except Exception as e:
                _handle_request_errors(e)
                raise e
            nonce = response.headers.get("DPoP-Nonce", "")
            if nonce and nonce != self._oauth.get_pds_nonce():
                self._oauth.save_pds_nonce(nonce)
            retriable = attempt < self._MAX_ATTEMPTS - 1
            if response.status_code in (400, 401) and _is_use_dpop_nonce(response):
                if nonce and retriable:
                    continue
            elif response.status_code == 401 and not refreshed and retriable:
                # server rejected the token before its recorded expiry
                refreshed = True
                self._oauth.get_access_token(force_refresh=True)
                continue
            break
        return _handle_response(response)
