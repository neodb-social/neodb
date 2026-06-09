import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from mastodon.models.bluesky import PROFILE_NSID, BlueskyAccount
from users.models import User


def _jcs(data: dict) -> bytes:
    # independent JCS (RFC 8785) canonicalization for verification
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _page(attr, dids, cursor):
    return SimpleNamespace(
        cursor=cursor, **{attr: [SimpleNamespace(did=d) for d in dids]}
    )


def test_paginate_dids_walks_cursor():
    account = BlueskyAccount()
    pages = [
        _page("follows", ["did:a", "did:b"], "c1"),
        _page("follows", ["did:c"], None),
    ]
    seen: list[str | None] = []

    def fetch(cursor):
        seen.append(cursor)
        return pages.pop(0)

    dids = account._paginate_dids(fetch, "follows")

    assert dids == ["did:a", "did:b", "did:c"]
    assert seen == [None, "c1"]  # second call carries the first page's cursor


def test_paginate_dids_bounded_by_max_pages():
    account = BlueskyAccount()

    def fetch(cursor):  # never-ending cursor
        return _page("mutes", ["did:x"], "more")

    dids = account._paginate_dids(fetch, "mutes", max_pages=3)

    assert dids == ["did:x", "did:x", "did:x"]


@pytest.mark.django_db(databases="__all__")
def test_profile_record_signed_and_verifiable(monkeypatch):
    user = User.register(email="prof@example.com", username="profuser")
    account = BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:prof", handle="prof.example"
    )
    puts: dict = {}
    monkeypatch.setattr(
        account,
        "put_record",
        lambda c, rk, r: puts.update({(c, rk): r}) or {"uri": "u", "cid": "c"},
    )
    monkeypatch.setattr(account, "delete_record", lambda c, rk: None)

    account.sync_profile_record()

    identity = user.identity
    record = puts[(PROFILE_NSID, "self")]
    assert record["did"] == "did:plc:prof"  # bound against cross-repo replay
    assert record["actor"] == identity.actor_uri
    assert record["handle"] == identity.full_handle
    assert record["url"].endswith(identity.url)
    proof = dict(record["proof"])
    assert proof["type"] == "DataIntegrityProof"
    assert proof["cryptosuite"] == "rsa-pkcs1-sha256-jcs"
    assert proof["proofPurpose"] == "assertionMethod"
    assert proof["verificationMethod"] == identity.takahe_identity.public_key_id
    # re-run the documented verification procedure against the actor's
    # published public key; verify() raises InvalidSignature otherwise
    proof_value = proof.pop("proofValue")
    document = {k: v for k, v in record.items() if k != "proof"}
    data = (
        hashlib.sha256(_jcs(proof)).digest() + hashlib.sha256(_jcs(document)).digest()
    )
    public_key = serialization.load_pem_public_key(
        identity.takahe_identity.public_key.encode()
    )
    assert isinstance(public_key, rsa.RSAPublicKey)
    public_key.verify(
        base64.b64decode(proof_value), data, padding.PKCS1v15(), hashes.SHA256()
    )


@pytest.mark.django_db(databases="__all__")
def test_profile_record_removed_when_not_discoverable(monkeypatch):
    user = User.register(email="hid@example.com", username="hiduser")
    takahe_identity = user.identity.takahe_identity
    takahe_identity.discoverable = False
    takahe_identity.save()
    account = BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:hid", handle="hid.example"
    )
    deletes: list = []
    monkeypatch.setattr(
        account, "put_record", lambda c, rk, r: {"uri": "u", "cid": "c"}
    )
    monkeypatch.setattr(account, "delete_record", lambda c, rk: deletes.append((c, rk)))

    account.sync_profile_record()

    assert (PROFILE_NSID, "self") in deletes


@pytest.mark.django_db(databases="__all__")
def test_register_with_account_schedules_sync(monkeypatch):
    called = []
    monkeypatch.setattr(
        User, "sync_accounts_later", lambda self: called.append(self.pk)
    )
    account = BlueskyAccount.objects.create(
        domain="-", uid="did:plc:reg", handle="reg.example"
    )
    user = User.register(username="reguser", account=account)

    # the account is linked and a sync is scheduled so the profile
    # record gets published now that the user exists
    account.refresh_from_db()
    assert account.user == user
    assert called == [user.pk]


def _stub_client(captured):
    """A minimal atproto client capturing the created feed post record."""

    def _create(repo, record):
        captured["repo"] = repo
        captured["record"] = record
        return SimpleNamespace(cid="cid1", uri="at://did:plc:poster/post/1")

    return SimpleNamespace(
        get_current_time_iso=lambda: "2026-06-09T00:00:00.000Z",
        app=SimpleNamespace(
            bsky=SimpleNamespace(
                feed=SimpleNamespace(post=SimpleNamespace(create=_create))
            )
        ),
    )


def test_post_attaches_fediverse_origin_url():
    account = BlueskyAccount(uid="did:plc:poster")
    captured: dict = {}
    account._client = _stub_client(captured)  # populate the cached_property

    r = account.post("hello", fediverse_uri="https://nd.test/@u/posts/1/")

    assert r == {"cid": "cid1", "id": "at://did:plc:poster/post/1"}
    assert captured["repo"] == "did:plc:poster"
    dumped = captured["record"].model_dump(by_alias=True, exclude_none=True)
    # off-lexicon Bridgy-style field pointing at the originating fediverse post
    assert dumped["neodbOriginalUrl"] == "https://nd.test/@u/posts/1/"
    assert dumped["text"] == "hello"


def test_post_without_fediverse_uri_has_no_origin_field():
    account = BlueskyAccount(uid="did:plc:poster")
    captured: dict = {}
    account._client = _stub_client(captured)

    account.post("hello")

    dumped = captured["record"].model_dump(by_alias=True, exclude_none=True)
    assert "neodbOriginalUrl" not in dumped
    # no user/language set -> langs omitted so Bluesky can auto-detect
    assert "langs" not in dumped


@pytest.mark.django_db(databases="__all__")
def test_post_tags_user_macrolanguage():
    user = User.register(email="lang@example.com", username="languser")
    user.language = "zh-Hans"  # macrolanguage -> "zh"
    account = BlueskyAccount(uid="did:plc:poster")
    account.user = user
    captured: dict = {}
    account._client = _stub_client(captured)

    account.post("你好")

    dumped = captured["record"].model_dump(by_alias=True, exclude_none=True)
    assert dumped["langs"] == ["zh"]
