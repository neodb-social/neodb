"""Configuration-driven OIDC Authorization Code + PKCE verification for OneID.

The configured provider's discovery document supplies endpoint names and JWKS.
No provider-specific Tencent endpoints or claims are embedded here.  This
module verifies an ID token before any Product binding or session operation;
it never persists exchanged OAuth credentials.
"""

import base64
import hashlib
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse

_SESSION_KEY = "oneid_oidc"
_SUPPORTED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


class OneIDError(Exception):
    """Base class for expected OneID configuration/provider/validation errors."""


class OneIDConfigurationError(OneIDError):
    pass


class OneIDProviderError(OneIDError):
    pass


class OneIDValidationError(OneIDError):
    pass


@dataclass(frozen=True)
class VerifiedManagedIdentity:
    """The verified immutable OneID anchor plus non-authoritative attributes."""

    issuer: str
    subject: str
    accepted_source_attributes: Mapping[str, Any]


@dataclass(frozen=True)
class OneIDConfig:
    issuer: str
    client_id: str
    client_secret: str
    discovery_url: str
    redirect_uri: str
    scope: str
    subject_claim: str
    accepted_source_attributes: tuple[str, ...]
    clock_skew: int
    timeout: float

    @classmethod
    def from_settings(cls) -> OneIDConfig:
        issuer = _normalise_issuer(getattr(settings, "ONEID_ISSUER", ""))
        client_id = getattr(settings, "ONEID_CLIENT_ID", "")
        if not issuer or not client_id:
            raise OneIDConfigurationError(
                "ONEID_ISSUER and ONEID_CLIENT_ID must be configured"
            )
        discovery_url = getattr(settings, "ONEID_DISCOVERY_URL", "") or (
            f"{issuer}/.well-known/openid-configuration"
        )
        redirect_uri = getattr(settings, "ONEID_REDIRECT_URI", "") or (
            settings.SITE_INFO["site_url"].rstrip("/") + reverse("users:oneid_callback")
        )
        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret=getattr(settings, "ONEID_CLIENT_SECRET", ""),
            discovery_url=discovery_url,
            redirect_uri=redirect_uri,
            scope=getattr(settings, "ONEID_SCOPE", "openid"),
            subject_claim=getattr(settings, "ONEID_SUBJECT_CLAIM", "sub"),
            accepted_source_attributes=tuple(
                getattr(settings, "ONEID_ACCEPTED_SOURCE_ATTRIBUTES", [])
            ),
            clock_skew=int(getattr(settings, "ONEID_CLOCK_SKEW", 60)),
            timeout=float(getattr(settings, "ONEID_HTTP_TIMEOUT", 10.0)),
        )


class OneIDClient:
    """Small OIDC client whose only Product result is a verified identity."""

    def __init__(self, config: OneIDConfig | None = None):
        self.config = config or OneIDConfig.from_settings()

    def _metadata(self) -> dict[str, Any]:
        _require_https(self.config.discovery_url, "discovery URL")
        try:
            response = httpx.get(self.config.discovery_url, timeout=self.config.timeout)
            response.raise_for_status()
            metadata = response.json()
        except Exception as exc:
            raise OneIDProviderError("OneID discovery request failed") from exc
        if not isinstance(metadata, dict):
            raise OneIDProviderError("OneID discovery response is not an object")
        if _normalise_issuer(metadata.get("issuer", "")) != self.config.issuer:
            raise OneIDValidationError("OneID discovery issuer mismatch")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                raise OneIDProviderError(f"OneID discovery missing {key}")
            _require_https(metadata[key], key)
        return metadata

    def authorization_url(self, request: HttpRequest) -> str:
        metadata = self._metadata()
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        nonce = secrets.token_urlsafe(32)
        challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
        request.session[_SESSION_KEY] = {
            "state": state,
            "code_verifier": code_verifier,
            "nonce": nonce,
            "issuer": self.config.issuer,
            "redirect_uri": self.config.redirect_uri,
        }
        return (
            metadata["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": self.config.client_id,
                    "redirect_uri": self.config.redirect_uri,
                    "scope": self.config.scope,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    def verify_callback(self, request: HttpRequest) -> VerifiedManagedIdentity:
        pending = request.session.pop(_SESSION_KEY, None)
        state = request.GET.get("state", "")
        code = request.GET.get("code", "")
        pending_state = pending.get("state") if isinstance(pending, dict) else None
        if (
            not isinstance(pending, dict)
            or not isinstance(state, str)
            or not state
            or not isinstance(pending_state, str)
            or not secrets.compare_digest(pending_state, state)
        ):
            raise OneIDValidationError("OneID state mismatch")
        if not all(
            isinstance(pending.get(key), str) and pending[key]
            for key in ("code_verifier", "nonce", "issuer", "redirect_uri")
        ):
            raise OneIDValidationError("OneID callback state is invalid")
        if not code:
            if request.GET.get("error"):
                raise OneIDProviderError("OneID authorization was not completed")
            raise OneIDValidationError("OneID callback has no authorization code")
        if pending.get("issuer") != self.config.issuer:
            raise OneIDValidationError("OneID callback configuration changed")

        metadata = self._metadata()
        token_response = self._exchange_code(
            metadata["token_endpoint"],
            code,
            pending["code_verifier"],
            pending["redirect_uri"],
        )
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OneIDValidationError("OneID token response has no ID token")
        claims = self._verify_jwt(id_token, metadata["jwks_uri"])
        self._validate_claims(claims, pending["nonce"])
        subject = claims.get(self.config.subject_claim)
        if not isinstance(subject, str) or not subject:
            raise OneIDValidationError("OneID token has no stable subject")
        if len(subject) > 255:
            raise OneIDValidationError("OneID subject is too long")
        attrs = {
            name: claims[name]
            for name in self.config.accepted_source_attributes
            if name in claims
        }
        return VerifiedManagedIdentity(
            issuer=self.config.issuer,
            subject=subject,
            accepted_source_attributes=attrs,
        )

    def _exchange_code(
        self, token_endpoint: str, code: str, code_verifier: str, redirect_uri: str
    ) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        if self.config.client_secret:
            data["client_secret"] = self.config.client_secret
        try:
            response = httpx.post(
                token_endpoint, data=data, timeout=self.config.timeout
            )
            response.raise_for_status()
            token_response = response.json()
        except Exception as exc:
            raise OneIDProviderError(
                "OneID authorization code exchange failed"
            ) from exc
        if not isinstance(token_response, dict):
            raise OneIDProviderError("OneID token response is not an object")
        if token_response.get("error"):
            raise OneIDProviderError("OneID authorization code exchange was rejected")
        return token_response

    def _verify_jwt(self, token: str, jwks_uri: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise OneIDValidationError("malformed OneID ID token") from exc
        algorithm = header.get("alg")
        if algorithm not in _SUPPORTED_ALGORITHMS:
            raise OneIDValidationError("unsupported OneID ID token algorithm")
        kid = header.get("kid")
        if kid is not None and not isinstance(kid, str):
            raise OneIDValidationError("invalid OneID key id")
        jwks = self._jwks(jwks_uri)
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise OneIDProviderError("OneID JWKS has no keys")
        candidates = [key for key in keys if isinstance(key, dict)]
        if kid is not None:
            candidates = [key for key in candidates if key.get("kid") == kid]
        elif len(candidates) != 1:
            raise OneIDValidationError("OneID ID token key is ambiguous")
        if len(candidates) != 1:
            raise OneIDValidationError("OneID ID token key was not found")
        try:
            key = jwt.PyJWK.from_dict(candidates[0]).key
        except (jwt.PyJWKError, ValueError, TypeError) as exc:
            raise OneIDValidationError("invalid OneID JWKS key") from exc
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                audience=self.config.client_id,
                options={"require": ["iss", "aud", "exp", self.config.subject_claim]},
                leeway=self.config.clock_skew,
            )
        except jwt.InvalidTokenError as exc:
            raise OneIDValidationError("OneID ID token signature invalid") from exc
        if not isinstance(claims, dict):
            raise OneIDValidationError("OneID ID token claims are not an object")
        return claims

    def _jwks(self, jwks_uri: str) -> dict[str, Any]:
        try:
            response = httpx.get(jwks_uri, timeout=self.config.timeout)
            response.raise_for_status()
            jwks = response.json()
        except Exception as exc:
            raise OneIDProviderError("OneID JWKS request failed") from exc
        if not isinstance(jwks, dict):
            raise OneIDProviderError("OneID JWKS response is not an object")
        return jwks

    def _validate_claims(self, claims: Mapping[str, Any], expected_nonce: str) -> None:
        if _normalise_issuer(claims.get("iss", "")) != self.config.issuer:
            raise OneIDValidationError("OneID token issuer mismatch")
        audience = claims.get("aud")
        if isinstance(audience, str):
            audiences = [audience]
        elif isinstance(audience, list) and all(
            isinstance(item, str) for item in audience
        ):
            audiences = audience
        else:
            raise OneIDValidationError("OneID token audience is invalid")
        if self.config.client_id not in audiences:
            raise OneIDValidationError("OneID token audience mismatch")
        if len(audiences) > 1 and claims.get("azp") != self.config.client_id:
            raise OneIDValidationError("OneID token authorized party mismatch")
        now = time.time()
        exp = claims.get("exp")
        if not _is_number(exp) or exp < now - self.config.clock_skew:
            raise OneIDValidationError("OneID token is expired")
        nbf = claims.get("nbf")
        if nbf is not None and (
            not _is_number(nbf) or nbf > now + self.config.clock_skew
        ):
            raise OneIDValidationError("OneID token is not yet valid")
        if claims.get("nonce") != expected_nonce:
            raise OneIDValidationError("OneID token nonce mismatch")
        subject = claims.get(self.config.subject_claim)
        if not isinstance(subject, str) or not subject:
            raise OneIDValidationError("OneID token has no stable subject")


def _normalise_issuer(value: Any) -> str:
    return value.rstrip("/") if isinstance(value, str) else ""


def _require_https(url: str, what: str) -> None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise OneIDConfigurationError(f"invalid OneID {what}")
    if not settings.DEBUG and not url.startswith("https://"):
        raise OneIDConfigurationError(f"OneID {what} must use HTTPS")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
