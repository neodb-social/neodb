import base64
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings

from mastodon.models.bluesky import (
    PROFILE_NSID,
    PUBLICATION_NSID,
    BlueskyAccount,
    EmbedObj,
)
from users.models import User

_TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"


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


def test_publication_rkey_is_valid_stable_tid():
    created = datetime(2026, 7, 1, tzinfo=timezone.utc)
    a = BlueskyAccount(uid="did:plc:one")
    a.created = created
    a.pk = 42
    b = BlueskyAccount(uid="did:plc:one")
    b.created = created
    b.pk = 42
    rkey = a.publication_rkey
    # the site.standard.publication lexicon requires tid record keys, and
    # the key must be reconstructable without stored state
    assert len(rkey) == 13
    assert all(c in _TID_ALPHABET for c in rkey)
    assert b.publication_rkey == rkey
    assert a.publication_uri == f"at://did:plc:one/{PUBLICATION_NSID}/{rkey}"


@pytest.mark.django_db(databases="__all__")
def test_publication_record_synced(monkeypatch):
    user = User.register(email="pub@example.com", username="pubuser")
    account = BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:pub", handle="pub.example"
    )
    puts: dict = {}
    monkeypatch.setattr(
        account,
        "put_record",
        lambda c, rk, r: puts.update({(c, rk): r}) or {"uri": "u", "cid": "c"},
    )

    ref = account.sync_publication_record()

    assert ref == {"uri": "u", "cid": "c"}
    identity = user.identity
    record = puts[(PUBLICATION_NSID, account.publication_rkey)]
    assert record["$type"] == PUBLICATION_NSID
    assert record["url"] == (settings.SITE_INFO["site_url"] + identity.url).rstrip("/")
    assert not record["url"].endswith("/")  # spec: base url without trailing slash
    assert record["name"] == identity.display_name
    assert record["preferences"] == {"showInDiscover": True}
    assert "icon" not in record  # test identity has no avatar file


@pytest.mark.django_db(databases="__all__")
def test_publication_record_removed_when_not_discoverable(monkeypatch):
    user = User.register(email="hidpub@example.com", username="hidpubuser")
    takahe_identity = user.identity.takahe_identity
    takahe_identity.discoverable = False
    takahe_identity.save()
    account = BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:hidpub", handle="hidpub.example"
    )
    deletes: list = []
    monkeypatch.setattr(
        account, "put_record", lambda c, rk, r: {"uri": "u", "cid": "c"}
    )
    monkeypatch.setattr(account, "delete_record", lambda c, rk: deletes.append((c, rk)))

    assert account.sync_publication_record() is None
    assert (PUBLICATION_NSID, account.publication_rkey) in deletes
    # no record should exist, so nothing to embed either
    assert account.get_publication_ref() is None


@pytest.mark.django_db(databases="__all__")
def test_get_publication_ref_reads_existing_record():
    user = User.register(email="pref@example.com", username="prefuser")
    account = BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:pref", handle="pref.example"
    )
    uri = account.publication_uri
    account._client = SimpleNamespace(
        com=SimpleNamespace(
            atproto=SimpleNamespace(
                repo=SimpleNamespace(
                    get_record=lambda params: SimpleNamespace(uri=uri, cid="c9")
                )
            )
        )
    )
    assert account.get_publication_ref() == {"uri": uri, "cid": "c9"}


@pytest.mark.django_db(databases="__all__")
def test_get_publication_ref_writes_missing_record(monkeypatch):
    user = User.register(email="mref@example.com", username="mrefuser")
    account = BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:mref", handle="mref.example"
    )

    def missing(params):
        raise Exception("RecordNotFound")

    account._client = SimpleNamespace(
        com=SimpleNamespace(
            atproto=SimpleNamespace(repo=SimpleNamespace(get_record=missing))
        )
    )
    ref = {"uri": account.publication_uri, "cid": "c1"}
    monkeypatch.setattr(account, "sync_publication_record", lambda: ref)
    assert account.get_publication_ref() == ref


def test_post_attaches_associated_refs():
    account = BlueskyAccount(uid="did:plc:poster")
    captured: dict = {}
    account._client = _stub_client(captured)
    obj = EmbedObj("T", "desc", "https://nd.test/review/x")
    refs = [
        {"uri": "at://did:plc:poster/site.standard.publication/3pub", "cid": "pc"},
        {"uri": "at://did:plc:poster/site.standard.document/3doc", "cid": "dc"},
    ]

    account.post("read ##obj##", obj=obj, associated_refs=refs)

    dumped = captured["record"].model_dump(by_alias=True, exclude_none=True)
    external = dumped["embed"]["external"]
    assert external["uri"] == "https://nd.test/review/x"
    assert external["associatedRefs"] == [
        {"$type": "com.atproto.repo.strongRef", "uri": r["uri"], "cid": r["cid"]}
        for r in refs
    ]

    account.post("read ##obj##", obj=obj)
    dumped = captured["record"].model_dump(by_alias=True, exclude_none=True)
    assert "associatedRefs" not in dumped["embed"]["external"]


@pytest.mark.django_db(databases="__all__")
def test_standard_site_publication_wellknown(client):
    user = User.register(email="wk@example.com", username="wkuser")
    url = f"/users/{user.username}/.well-known/site.standard.publication"
    assert client.get(url).status_code == 404  # no linked account

    account = BlueskyAccount.objects.create(
        user=user, domain="-", uid="did:plc:wk", handle="wk.example"
    )
    response = client.get(url)
    assert response.status_code == 200
    assert response["content-type"].startswith("text/plain")
    assert response.content.decode() == account.publication_uri

    takahe_identity = user.identity.takahe_identity
    takahe_identity.discoverable = False
    takahe_identity.save()
    assert client.get(url).status_code == 404


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
