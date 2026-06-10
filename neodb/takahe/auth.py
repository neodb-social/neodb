"""HTTP signature authentication for inbound NeoDB-to-NeoDB AP GETs.

Scope: this verifier intentionally accepts only the canonical signed-GET
shape that our own outbound code emits. It is NOT a general-purpose AP
HTTP-signature implementation — Takahe's inbox has that. Keeping the rules
narrow lets the verifier stay short and obvious.

Required signature shape:

    GET /<path> HTTP/1.1
    Host: <host>
    Date: <RFC1123>
    Signature: keyId="<actor#main-key>",algorithm="rsa-sha256",
               headers="(request-target) host date",signature="<b64>"

Anything else (extra/missing signed headers, hs2019 alias, body/digest,
unknown signer, stale Date, malformed base64) is rejected outright.
"""

import base64
import binascii
import re
import time
from email.utils import formatdate
from functools import wraps
from typing import Callable
from urllib.parse import urldefrag, urlparse

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.http import parse_http_date
from loguru import logger

from takahe.models import Config, Identity
from takahe.utils import Takahe

_DATE_SKEW_LIMIT = 300
_REQUIRED_HEADERS = ("(request-target)", "host", "date")
_KV_RE = re.compile(r'(\w+)="([^"]*)"')


class _SigError(Exception):
    """Raised on any verification failure. Mapped to 401 by the decorator."""


def _parse_signature_header(header: str) -> dict[str, str]:
    parts = dict(_KV_RE.findall(header))
    for key in ("keyId", "algorithm", "headers", "signature"):
        if key not in parts:
            raise _SigError(f"Signature header missing {key}")
    if parts["algorithm"] != "rsa-sha256":
        raise _SigError(f"Unsupported algorithm {parts['algorithm']}")
    if tuple(parts["headers"].split(" ")) != _REQUIRED_HEADERS:
        raise _SigError("Signed headers must be exactly: (request-target) host date")
    return parts


def _signing_string(request: HttpRequest) -> bytes:
    host = request.headers.get("Host")
    date = request.headers.get("Date")
    method = request.method or "GET"
    if not host or not date:
        raise _SigError("Host and Date headers required")
    # ``get_full_path`` includes the query string; HTTP-signature spec requires
    # the (request-target) value to be the full request URI path+query.
    return (
        f"(request-target): {method.lower()} {request.get_full_path()}\n"
        f"host: {host}\ndate: {date}"
    ).encode("utf-8")


def _check_date_skew(date_header: str) -> None:
    try:
        ts = parse_http_date(date_header)
    except (ValueError, OverflowError) as e:
        raise _SigError(f"Invalid Date header: {e}") from e
    if abs(time.time() - ts) > _DATE_SKEW_LIMIT:
        raise _SigError("Date skew exceeds limit")


def verify_http_signature(request: HttpRequest):
    """Verify the request and return the signing ``APIdentity``."""
    sig_header = request.headers.get("Signature")
    if not sig_header:
        raise _SigError("Signature header required")
    details = _parse_signature_header(sig_header)
    _check_date_skew(request.headers.get("Date", ""))
    try:
        signature = base64.b64decode(details["signature"])
    except (ValueError, binascii.Error) as e:
        raise _SigError(f"Bad base64 signature: {e}") from e
    key_id_actor = urldefrag(details["keyId"]).url
    signer = Identity.objects.filter(actor_uri=key_id_actor).first()
    if not signer or not signer.public_key:
        # Don't fetch synchronously: that would let unsigned probes drive
        # outbound HTTP. Federation push primes the cache.
        raise _SigError(f"Unknown signer {key_id_actor}")
    public_key = serialization.load_pem_public_key(signer.public_key.encode("ascii"))
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise _SigError("Signer key is not RSA")
    try:
        public_key.verify(
            signature, _signing_string(request), padding.PKCS1v15(), hashes.SHA256()
        )
    except InvalidSignature as e:
        raise _SigError("Signature mismatch") from e
    return Takahe.get_or_create_remote_apidentity(signer)


def _system_actor_signing_keys() -> tuple[str, str]:
    """Return ``(public_key_id, private_key_pem)`` for Takahe's SystemActor.

    Keys are stored in the shared Takahe ``Config`` table under
    ``system_actor_*`` rows scoped to no user/identity/domain.
    """
    rows = {
        c.key: c.json
        for c in Config.objects.filter(
            user=None,
            identity=None,
            domain=None,
            key__in=["system_actor_private_key", "system_actor_public_key"],
        )
    }
    private_key = rows.get("system_actor_private_key")
    if not private_key:
        raise RuntimeError(
            "Takahe SystemActor keys not found; run Takahe's "
            "generate_keys_if_needed once before federated GETs."
        )
    actor_uri = f"https://{settings.SITE_INFO['site_domain']}/actor/"
    return actor_uri + "#main-key", private_key


def sign_get(url: str, *, timeout: float = 10.0) -> httpx.Response:
    """Make a NeoDB-flavored signed GET request and return the httpx response.

    Signs the same canonical shape that ``verify_http_signature`` accepts:
    ``(request-target) host date`` over RSA-SHA256 with the SystemActor key.
    Caller is responsible for parsing the response body.

    SSRF gate: rejects malformed, non-HTTP(S), and private/loopback URLs.
    Callers also validate at persist time (see ``Collection.update_by_ap_object``);
    this is defense-in-depth against an at-rest URL ever sneaking through.
    """
    from common.validators import is_valid_url

    if not is_valid_url(url):
        raise ValueError(f"sign_get: refusing unsafe URL {url!r}")
    key_id, private_key_pem = _system_actor_signing_keys()
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    date = formatdate(timeval=time.time(), usegmt=True)
    cleartext = (f"(request-target): get {path}\nhost: {host}\ndate: {date}").encode(
        "utf-8"
    )
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("ascii"), password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise RuntimeError("SystemActor key is not RSA")
    signature = private_key.sign(cleartext, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(signature).decode("ascii")
    sig_header = (
        f'keyId="{key_id}",algorithm="rsa-sha256",'
        f'headers="(request-target) host date",signature="{sig_b64}"'
    )
    headers = {
        "Date": date,
        "Host": host,
        "Signature": sig_header,
        "Accept": "application/activity+json",
        "User-Agent": "NeoDB-Federation/1.0",
    }
    return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=False)


def http_signature_required(view_func: Callable) -> Callable:
    """View decorator that requires a valid NeoDB-flavored AP HTTP signature.

    On success, ``request.signed_identity`` is set to the signer's remote
    ``APIdentity``. On failure the decorator returns ``401 Unauthorized``.
    """

    @wraps(view_func)
    def wrapped(request: HttpRequest, *args, **kwargs):
        try:
            # ``signed_identity`` is attached dynamically; downstream views
            # read it via ``getattr`` or a typed shim.
            setattr(request, "signed_identity", verify_http_signature(request))
        except _SigError as e:
            logger.info(f"HTTP signature rejected: {e}")
            return HttpResponse(str(e), status=401, content_type="text/plain")
        return view_func(request, *args, **kwargs)

    return wrapped
