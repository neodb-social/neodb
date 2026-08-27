import base64
import time

import httpx
import pytest
from django.test.client import RequestFactory
from pytest_httpx import HTTPXMock

from core.files import check_url_safety
from core.signatures import (
    HttpSignature,
    LDSignature,
    VerificationError,
    VerificationFormatError,
)


def test_sign_ld(keypair):
    """
    Tests signing JSON-LD documents by round-tripping them through the
    verifier.
    """
    # Create the signature
    document = {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": "https://example.com/test-create",
        "type": "Create",
        "actor": "https://example.com/test-actor",
        "object": {
            "id": "https://example.com/test-object",
            "type": "Note",
        },
    }
    signature_section = LDSignature.create_signature(
        document,
        keypair["private_key"],
        keypair["public_key_id"],
    )
    # Check it and assign it to the document
    assert "signatureValue" in signature_section
    assert signature_section["type"] == "RsaSignature2017"
    document["signature"] = signature_section
    # Now verify it ourselves
    LDSignature.verify_signature(document, keypair["public_key"])


def test_verifying_ld(keypair):
    """
    Tests verifying JSON-LD signatures from a known-good document
    """
    document = {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": "https://example.com/test-create",
        "type": "Create",
        "actor": "https://example.com/test-actor",
        "object": {"id": "https://example.com/test-object", "type": "Note"},
        "signature": {
            "@context": "https://w3id.org/identity/v1",
            "creator": "https://example.com/test-actor#test-key",
            "created": "2023-10-25T08:08:47.702Z",
            "signatureValue": "ajg4ukZzCtBWjflO1u6MlTc4tBVO6MsqzBr/L+kO5VI2ucutFaUdDa/Kx4W12ZCm9oYvTyMQMnoeELx5BifslRWEeMmo1wWMPXmg2/BMKgm8Spt+Zanq68uTlYGyKvuw1Q0FyNq84N2PbRZRXu2Yhlj2KnAVTRtKrsfEiCg3yNfVQ7lbUpDtlXvXLAq2yBN8H/BnZDoynjaDlafFW9Noq8025q1K/lz5jNzBEL22CSrKsD2qYWq1TK3s3h6SJ+j3J+5s0Ni3F/TH7W/5VeGBpzx4z6MSjmn7aHAS3JNCnAWDW9Rf6yKLg2y5htj6FpexiGcoEjO3VqjLoIP4f/115Q==",
            "type": "RsaSignature2017",
        },
    }
    # Ensure it verifies with correct data
    LDSignature.verify_signature(document, keypair["public_key"])
    # signature should remain in document if it was valid
    assert "signature" in document
    # Mutate it slightly and ensure it does not verify
    with pytest.raises(VerificationError):
        document["actor"] = "https://example.com/evil-actor"
        LDSignature.verify_signature(document, keypair["public_key"])


def test_sign_http(httpx_mock: HTTPXMock, keypair):
    """
    Tests signing HTTP requests by round-tripping them through our verifier
    """
    # Create document
    document = {
        "id": "https://example.com/test-create",
        "type": "Create",
        "actor": "https://example.com/test-actor",
        "object": {
            "id": "https://example.com/test-object",
            "type": "Note",
        },
    }
    # Send the signed request to the mock library
    httpx_mock.add_response()
    HttpSignature.signed_request(
        uri="https://example.com/test-actor",
        body=document,
        private_key=keypair["private_key"],
        key_id=keypair["public_key_id"],
    )
    # Retrieve it and construct a fake request object
    outbound_request = httpx_mock.get_request()
    fake_request = RequestFactory().post(
        path="/test-actor",
        data=outbound_request.content,
        content_type=outbound_request.headers["content-type"],
        HTTP_HOST="example.com",
        HTTP_DATE=outbound_request.headers["date"],
        HTTP_SIGNATURE=outbound_request.headers["signature"],
        HTTP_DIGEST=outbound_request.headers["digest"],
    )
    # Verify that
    HttpSignature.verify_request(fake_request, keypair["public_key"])


def test_verify_http(keypair):
    """
    Tests verifying HTTP requests against a known good example
    """
    # Make our predictable request
    fake_request = RequestFactory().post(
        path="/test-actor",
        data=b'{"id": "https://example.com/test-create", "type": "Create", "actor": "https://example.com/test-actor", "object": {"id": "https://example.com/test-object", "type": "Note"}}',
        content_type="application/json",
        HTTP_HOST="example.com",
        HTTP_DATE="Sat, 12 Nov 2022 21:57:18 GMT",
        HTTP_SIGNATURE='keyId="https://example.com/test-actor#test-key",headers="(request-target) host date digest content-type",signature="IRduYoDJIh90mprjUgOIdxY1iaBWHs5ou9vsDlcmSekg6DXMZTiXjmZxbNIrnpEbNFu3wTcqz1nv9H97Gp7orbYMuHm6j2ecxsvzSr37T9jxBbt3Ov3xSfuYWwhv6PuTWNxHtUQWNuAIc3wHDAQt8Flnak/uHe7swoAq4uHq2kt18iMW6CEV9XA5ESFho2HSUgRaifoNxJlIWbHYPJiP0t9aktgGBkpQoZ8ulOj3Ew4RwC1lwk9kzWiLIjU4tSAie8RbIy2g0aUvA1tQh9Uge1by3o7+349SL5iooj+B6WSCEvvjEl52wo3xoEQmv0ptYuSPLUgB9tP8q7DoHEc8Dw==",algorithm="rsa-sha256"',
        HTTP_DIGEST="SHA-256=07sIbQ3GlOHWMbFMNajtPNtmUQXXu20UuvrIYLlI3kc=",
    )
    # Verify that
    HttpSignature.verify_request(fake_request, keypair["public_key"], skip_date=True)


def test_verify_http_bad_signature(keypair):
    """
    Tests that a signature missing the algorithm does not work
    """
    # Make our predictable request
    fake_request = RequestFactory().post(
        path="/test-actor",
        data=b'{"id": "https://example.com/test-create", "type": "Create", "actor": "https://example.com/test-actor", "object": {"id": "https://example.com/test-object", "type": "Note"}}',
        content_type="application/json",
        HTTP_HOST="example.com",
        HTTP_DATE="Sat, 12 Nov 2022 21:57:18 GMT",
        HTTP_SIGNATURE='keyId="https://example.com/test-actor#test-key",headers="(request-target) host date digest content-type",signature="IRduYoDJIh90mprjUgOIdxY1iaBWHs5ou9vsDlcmSekg6DXMZTiXjmZxbNIrnpEbNFu3wTcqz1nv9H97Gp7orbYMuHm6j2ecxsvzSr37T9jxBbt3Ov3xSfuYWwhv6PuTWNxHtUQWNuAIc3wHDAQt8Flnak/uHe7swoAq4uHq2kt18iMW6CEV9XA5ESFho2HSUgRaifoNxJlIWbHYPJiP0t9aktgGBkpQoZ8ulOj3Ew4RwC1lwk9kzWiLIjU4tSAie8RbIy2g0aUvA1tQh9Uge1by3o7+349SL5iooj+B6WSCEvvjEl52wo3xoEQmv0ptYuSPLUgB9tP8q7DoHEc8Dw=="',
        HTTP_DIGEST="SHA-256=07sIbQ3GlOHWMbFMNajtPNtmUQXXu20UuvrIYLlI3kc=",
    )
    # Verify that
    with pytest.raises(VerificationError):
        HttpSignature.verify_request(
            fake_request, keypair["public_key"], skip_date=True
        )


def test_headers_from_request_missing_header_raises_format_error():
    """
    A header listed in the signature but absent from the request must raise
    VerificationFormatError (400), not a bare KeyError (which would 500).
    """
    request = RequestFactory().post(
        path="/inbox",
        data=b"{}",
        content_type="application/json",
        HTTP_HOST="example.com",
    )
    # "x-missing-header" is not present in the request
    with pytest.raises(VerificationFormatError):
        HttpSignature.headers_from_request(request, ["host", "x-missing-header"])


def test_headers_from_request_created_pseudo_header(keypair):
    """
    (created) pseudo-header is resolved from signature_params, not HTTP headers.
    """
    request = RequestFactory().post(
        path="/inbox",
        data=b"{}",
        content_type="application/json",
        HTTP_HOST="example.com",
    )
    params = {
        "created": "1700000000",
        "keyid": "k",
        "algorithm": "hs2019",
        "headers": ["(created)", "host"],
        "signature": b"",
    }
    result = HttpSignature.headers_from_request(request, ["(created)", "host"], params)
    assert "(created): 1700000000" in result
    assert "host: example.com" in result


def test_headers_from_request_created_missing_param_raises():
    """
    (created) in the headers list but no created param → VerificationFormatError.
    """
    request = RequestFactory().post(
        "/inbox", data=b"{}", content_type="application/json"
    )
    with pytest.raises(VerificationFormatError, match="created"):
        HttpSignature.headers_from_request(request, ["(created)"])


def test_verify_request_hs2019_with_created(keypair):
    """
    A valid hs2019 signature that uses (created) instead of date verifies correctly.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    created_ts = str(int(time.time()))
    body = b'{"type": "Note"}'
    digest = HttpSignature.calculate_digest(body)
    signed_string = (
        f"(request-target): post /inbox\n"
        f"host: example.com\n"
        f"(created): {created_ts}\n"
        f"digest: {digest}"
    )
    private_key = serialization.load_pem_private_key(
        keypair["private_key"].encode(), password=None
    )
    sig_bytes = private_key.sign(
        signed_string.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    sig_b64 = base64.b64encode(sig_bytes).decode()

    request = RequestFactory().post(
        path="/inbox",
        data=body,
        content_type="application/json",
        HTTP_HOST="example.com",
        HTTP_DIGEST=digest,
        HTTP_SIGNATURE=(
            f'keyId="{keypair["public_key_id"]}",'
            f'algorithm="hs2019",'
            f'headers="(request-target) host (created) digest",'
            f"created={created_ts},"
            f'signature="{sig_b64}"'
        ),
    )
    # Should verify without raising
    HttpSignature.verify_request(request, keypair["public_key"])


def test_verify_request_created_timestamp_too_old(keypair):
    """
    (created) timestamp outside the allowed window raises VerificationFormatError.
    """
    stale_ts = str(int(time.time()) - 600)  # 10 minutes ago

    request = RequestFactory().post(
        path="/inbox",
        data=b"{}",
        content_type="application/json",
        HTTP_HOST="example.com",
        HTTP_SIGNATURE=(
            f'keyId="{keypair["public_key_id"]}",'
            f'algorithm="hs2019",'
            f'headers="host (created)",'
            f"created={stale_ts},"
            f'signature="{base64.b64encode(b"x").decode()}"'
        ),
    )
    with pytest.raises(VerificationFormatError, match="too far away"):
        HttpSignature.verify_request(request, keypair["public_key"])


def test_verify_request_malformed_date_header(keypair):
    """
    A malformed Date header must raise VerificationFormatError (→ 400), not a
    bare ValueError / OverflowError that would escape as a 500 server error.
    """
    request = RequestFactory().post(
        path="/inbox",
        data=b"{}",
        content_type="application/json",
        HTTP_HOST="example.com",
        HTTP_DATE="not-a-real-date",
        HTTP_SIGNATURE=(
            f'keyId="{keypair["public_key_id"]}",'
            f'algorithm="rsa-sha256",'
            f'headers="host date",'
            f'signature="{base64.b64encode(b"x").decode()}"'
        ),
    )
    with pytest.raises(VerificationFormatError, match="Invalid Date header"):
        HttpSignature.verify_request(request, keypair["public_key"])


# Valid DNS and resolvable in a browser, but idna rejects the emoji codepoint.
# xn--4t8h is the punycode for a single pager emoji.
EMOJI_PUNYCODE_URI = "https://xn--4t8h.example/users/test-actor"


@pytest.fixture
def _enable_federation(settings):
    original = settings.SETUP.NO_FEDERATION
    settings.SETUP.NO_FEDERATION = False
    yield
    settings.SETUP.NO_FEDERATION = original


def test_signed_request_invalid_idna_host(keypair, monkeypatch, _enable_federation):
    """
    A host that is valid DNS but invalid IDNA2008 must surface inside the
    httpx.RequestError tree.

    httpx decodes punycode lazily in httpx.URL.host, and the SSRF hook is the
    first thing to read it, so httpx never gets to wrap the idna error. Every
    caller of signed_request guards on httpx.RequestError, so a raw idna error
    -- or a bare httpx.HTTPError, which is RequestError's *parent* -- escapes
    their handling and reaches Stator.
    """
    # conftest's autouse _bypass_ssrf_check stubs the hook out for every test;
    # restore it, since reading request.url.host is what raises in production.
    monkeypatch.setattr("core.signatures.check_url_safety", check_url_safety)

    with pytest.raises(httpx.RequestError) as exc_info:
        HttpSignature.signed_request(
            uri=EMOJI_PUNYCODE_URI,
            body=None,
            private_key=keypair["private_key"],
            key_id=keypair["public_key_id"],
            method="get",
        )
    assert isinstance(exc_info.value, httpx.ConnectError)
    assert "Invalid IDNA host" in str(exc_info.value)
