"""Tests for Collection federation across servers.

Covers the two-step model:

- ``Collection.ap_object`` (embedded in the announcement Note Post) carries
  metadata only, no ``orderedItems``.
- ``Collection.full_ap_object()`` (returned by the dereferenceable AP
  endpoint after a signed GET) carries the ordered member list.
- ``Collection.update_by_ap_object`` creates/updates the local mirror from
  the lightweight Note payload and schedules a member-fetch job.
- ``Collection._sync_members_from_ap`` upserts members atomically when
  given an items list (called from the fetch job).
- URL paste resolution maps a remote Collection URL to the local mirror
  while respecting visibility.
- AP content-negotiation in ``collection_retrieve`` routes signed AP
  fetches to the dereferenceable endpoint.

Gaps (not covered here, called out for future work):
- ``takahe.auth.verify_http_signature`` rejection branches in detail
  (covered only at the happy / generic-failure level here).
- ``takahe.auth.sign_get`` outbound signing (needs httpx + key fixtures).
- ``fetch_remote_collection_members`` job execution (needs httpx mock).
- ``_post_fetched`` Collection-branch dispatch end-to-end.
- list signal hooks bumping parent ``edited_time`` on member changes.
"""

import base64
import time
from email.utils import formatdate
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.test import RequestFactory

from catalog.models import Edition
from catalog.views.search import resolve_url_query
from journal.models import Collection
from users.models import User


def _make_remote_post(
    identity_pk: int,
    post_id: int = 91001,
    actor_uri: str = "https://remote.example/actor/",
) -> MagicMock:
    post = MagicMock()
    post.local = False
    post.visibility = 0
    post.id = post_id
    post.pk = post_id  # MagicMock attribute access doesn't proxy id -> pk
    post.author_id = identity_pk
    # ``Collection.update_by_ap_object`` matches the inbound ``id`` URL host
    # against the announcing author's actor URL host, so this must be a real
    # string (rather than the MagicMock proxy default) and share the host
    # used by the synthetic ``remote.example`` URLs the tests pass in.
    post.author.actor_uri = actor_uri
    post.summary = None
    post.sensitive = False
    post.attachments.all.return_value = []
    post.type_data = {"object": {"relatedWith": []}}
    return post


def _items(*books_with_notes):
    return [
        {
            "type": "CollectionItem",
            "withRegardTo": book.absolute_url,
            "itemType": "Edition",
            "note": note,
        }
        for book, note in books_with_notes
    ]


def _note_ap(remote_url: str, owner, **overrides) -> dict:
    """A lightweight Collection AP object as it would arrive embedded in a
    Note Post — no ``orderedItems``."""
    base = {
        "id": remote_url,
        "type": "Collection",
        "name": "Remote",
        "content": "",
        "mediaType": "text/markdown",
        "published": "2026-01-01T00:00:00+00:00",
        "updated": "2026-01-02T00:00:00+00:00",
        "attributedTo": owner.actor_uri,
        "href": remote_url,
        "totalItems": 0,
    }
    base.update(overrides)
    return base


@pytest.mark.django_db(databases="__all__")
class TestCollectionApShapes:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="colap@test.com", username="colap_user")
        self.identity = self.user.identity
        self.book1 = Edition.objects.create(title="Book One")
        self.book2 = Edition.objects.create(title="Book Two")

    def test_ap_object_is_lightweight(self):
        c = Collection.objects.create(
            owner=self.identity, title="Reading list", visibility=0
        )
        c.append_item(self.book1, note="first")
        c.append_item(self.book2, note="second")
        obj = c.ap_object
        assert obj["type"] == "Collection"
        assert obj["totalItems"] == 2
        assert "orderedItems" not in obj

    def test_full_ap_object_has_ordered_items(self):
        c = Collection.objects.create(
            owner=self.identity, title="Reading list", visibility=0
        )
        c.append_item(self.book1, note="first")
        c.append_item(self.book2, note="second")
        full = c.full_ap_object()
        assert full["totalItems"] == 2
        items = full["orderedItems"]
        assert items[0]["withRegardTo"] == self.book1.absolute_url
        assert items[0]["note"] == "first"
        assert items[1]["withRegardTo"] == self.book2.absolute_url
        assert "position" not in items[0]


@pytest.mark.django_db(databases="__all__")
class TestCollectionUpdateByApObject:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="colsync@test.com", username="colsync_user")
        self.identity = self.user.identity
        # ``is_valid_url`` does DNS resolution; the synthetic ``remote.example``
        # hosts in this fixture won't resolve in the CI sandbox. The test
        # exercises the federation logic, not the SSRF gate, so the gate is
        # short-circuited to True for every URL.
        self._url_patch = patch(
            "journal.models.collection.is_valid_url", return_value=True
        )
        self._url_patch.start()
        yield
        self._url_patch.stop()

    def test_create_mirror_enqueues_member_fetch(self):
        remote_url = "https://remote.example/collection/abc123abc123abc123abc1"
        ap_obj = _note_ap(remote_url, self.identity, name="Remote Picks")
        post = _make_remote_post(self.identity.pk)
        with patch("django_rq.get_queue") as gq:
            result = Collection.update_by_ap_object(self.identity, None, ap_obj, post)
            assert result is not None
            assert result.local is False
            assert result.remote_id == remote_url
            assert result.title == "Remote Picks"
            # Member fetch is scheduled asynchronously; the Note carries no items.
            gq.return_value.enqueue.assert_called_once()
            args = gq.return_value.enqueue.call_args.args
            assert args[0] == (
                "journal.jobs.collection_sync.fetch_remote_collection_members"
            )
            assert args[1] == result.pk
        assert result.members.count() == 0

    def test_stale_payload_is_ignored(self):
        remote_url = "https://remote.example/collection/old"
        ap_obj = _note_ap(remote_url, self.identity, name="Stable")
        post = _make_remote_post(self.identity.pk, post_id=91003)
        with patch("django_rq.get_queue"):
            col = Collection.update_by_ap_object(self.identity, None, ap_obj, post)
        assert col is not None
        original_edited = col.edited_time
        stale = dict(ap_obj)
        stale["updated"] = "2025-01-01T00:00:00+00:00"
        stale["name"] = "Should not apply"
        with patch("django_rq.get_queue"):
            result = Collection.update_by_ap_object(self.identity, None, stale, post)
        assert result is not None
        result.refresh_from_db()
        assert result.title == "Stable"
        assert result.edited_time == original_edited


@pytest.mark.django_db(databases="__all__")
class TestSyncMembersFromAp:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="syncm@test.com", username="syncm_user")
        self.identity = self.user.identity
        self.book1 = Edition.objects.create(title="Book One")
        self.book2 = Edition.objects.create(title="Book Two")
        self.book3 = Edition.objects.create(title="Book Three")

    def _make_mirror(self) -> Collection:
        col = Collection(
            owner=self.identity,
            local=False,
            remote_id="https://remote.example/collection/m",
            title="Mirror",
            visibility=0,
        )
        col.save(post_when_save=False, index_when_save=False)
        return col

    def test_initial_member_population(self):
        col = self._make_mirror()
        pending = Collection._sync_members_from_ap(
            col, _items((self.book1, "a"), (self.book2, "b"))
        )
        assert pending == 0
        members = list(col.members.order_by("position"))
        assert [m.item_id for m in members] == [self.book1.pk, self.book2.pk]
        assert [m.note for m in members] == ["a", "b"]

    def test_reorder_and_replace(self):
        col = self._make_mirror()
        Collection._sync_members_from_ap(
            col, _items((self.book1, "a"), (self.book2, "b"), (self.book3, "c"))
        )
        Collection._sync_members_from_ap(
            col, _items((self.book3, "c-new"), (self.book1, "a-new"))
        )
        members = list(col.members.order_by("position"))
        assert [m.item_id for m in members] == [self.book3.pk, self.book1.pk]
        assert [m.note for m in members] == ["c-new", "a-new"]

    def test_unknown_item_url_enqueues_fetch_and_reports_pending(self):
        col = self._make_mirror()
        unknown_item_url = "https://other.example/book/totally-new"
        items = [
            {
                "type": "CollectionItem",
                "withRegardTo": self.book1.absolute_url,
                "itemType": "Edition",
                "note": "known",
            },
            {
                "type": "CollectionItem",
                "withRegardTo": unknown_item_url,
                "itemType": "Edition",
                "note": "unknown",
            },
        ]
        with patch("journal.models.collection.enqueue_fetch") as enq:
            pending = Collection._sync_members_from_ap(col, items)
        assert pending == 1
        called_urls = [c.args[0] for c in enq.call_args_list]
        assert unknown_item_url in called_urls
        assert all(
            call.args[0] != self.book1.absolute_url for call in enq.call_args_list
        )
        assert [m.item_id for m in col.members.all()] == [self.book1.pk]


@pytest.mark.django_db(databases="__all__")
class TestRemoteCollectionUrlPaste:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="urlsearch@test.com", username="urlsearch_u")
        self.identity = self.user.identity
        # See TestCollectionUpdateByApObject for why we short-circuit the
        # SSRF gate inside the federation tests.
        self._url_patch = patch(
            "journal.models.collection.is_valid_url", return_value=True
        )
        self._url_patch.start()
        yield
        self._url_patch.stop()

    def test_resolve_url_returns_local_mirror(self):
        remote_url = "https://remote.example/collection/aaaaaaaaaaaaaaaaaaaaaa"
        ap_obj = _note_ap(remote_url, self.identity, name="Remote")
        post = _make_remote_post(self.identity.pk, post_id=92001)
        with patch("django_rq.get_queue"):
            Collection.update_by_ap_object(self.identity, None, ap_obj, post)
        col = Collection.objects.get(remote_id=remote_url)
        rf = RequestFactory().get("/search?q=" + remote_url)
        rf.user = self.user
        with patch("django_rq.get_queue") as gq:
            response = resolve_url_query(rf, remote_url)
        assert response is not None
        assert response.status_code in (302, 301)
        assert col.url in response["Location"]
        gq.return_value.enqueue.assert_called()

    def test_resolve_url_hides_invisible_remote_mirror(self):
        remote_url = "https://remote.example/collection/bbbbbbbbbbbbbbbbbbbbbb"
        ap_obj = _note_ap(remote_url, self.identity, name="Hidden")
        post = _make_remote_post(self.identity.pk, post_id=92002)
        post.visibility = 2  # Takahe Followers -> NeoDB visibility=1
        with patch("django_rq.get_queue"):
            Collection.update_by_ap_object(self.identity, None, ap_obj, post)
        intruder = User.register(email="intruder@test.com", username="intruder")
        rf = RequestFactory().get("/search?q=" + remote_url)
        rf.user = intruder
        # Stub the catalog fall-through fetcher: when the mirror is hidden
        # ``resolve_url_query`` continues into the regular catalog
        # ``fetch`` helper, which would render a template that needs
        # session middleware. We only care that no 302 to the local
        # mirror was emitted and that no resync was enqueued.
        sentinel = object()
        with (
            patch("django_rq.get_queue") as gq,
            patch("catalog.views.search.fetch", return_value=sentinel) as fetch_mock,
        ):
            response = resolve_url_query(rf, remote_url)
        assert not gq.return_value.enqueue.called
        assert response is sentinel
        fetch_mock.assert_called_once()


@pytest.mark.django_db(databases="__all__")
class TestApContentNegotiation:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="cn@test.com", username="cn_user")
        self.identity = self.user.identity
        self.collection = Collection.objects.create(
            owner=self.identity, title="Public", visibility=0
        )

    def test_html_view_when_no_ap_accept(self):
        from journal.views.collection import _wants_activitypub

        rf = RequestFactory().get(self.collection.url)
        assert _wants_activitypub(rf) is False

    def test_ap_view_when_ap_accept(self):
        from journal.views.collection import _wants_activitypub

        rf = RequestFactory().get(
            self.collection.url, HTTP_ACCEPT="application/activity+json"
        )
        assert _wants_activitypub(rf) is True

    def test_ap_view_unsigned_public_returns_200(self):
        # Public collections are returned to anonymous callers — same as
        # the HTML view. This lets first-time peers and SystemActor-signed
        # GETs (whose actor we may not have cached) dereference the
        # collection AP without first having to verify their identity.
        from journal.views.collection import _collection_ap_view

        rf = RequestFactory().get(self.collection.url)
        response = _collection_ap_view(rf, self.collection.uuid)
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/activity+json")

    def test_ap_view_unsigned_followers_only_returns_404(self):
        # Followers-only and private collections require a signed GET from
        # an authorized follower; an unsigned probe gets 404 (not 403) so
        # existence is not leaked.
        from journal.views.collection import _collection_ap_view

        c = Collection.objects.create(owner=self.identity, title="Hidden", visibility=1)
        rf = RequestFactory().get(c.url)
        response = _collection_ap_view(rf, c.uuid)
        assert response.status_code == 404


@pytest.mark.django_db(databases="__all__")
class TestVisibilityByIdentity:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.owner_user = User.register(email="ownr@test.com", username="ownr_user")
        self.owner = self.owner_user.identity

    def test_public_visible_to_anonymous(self):
        c = Collection.objects.create(owner=self.owner, title="P", visibility=0)
        assert c.is_visible_to_identity(None) is True

    def test_private_invisible_to_anonymous(self):
        c = Collection.objects.create(owner=self.owner, title="X", visibility=2)
        assert c.is_visible_to_identity(None) is False

    def test_private_visible_to_owner(self):
        c = Collection.objects.create(owner=self.owner, title="X", visibility=2)
        assert c.is_visible_to_identity(self.owner) is True


def _make_signed_get(
    private_key: rsa.RSAPrivateKey,
    *,
    key_id: str,
    path: str,
    host: str = "neodb.local",
    date: str | None = None,
    headers_list: str = "(request-target) host date",
    algorithm: str = "rsa-sha256",
):
    """Build a Django test request with a NeoDB-canonical HTTP signature."""
    if date is None:
        date = formatdate(timeval=time.time(), usegmt=True)
    cleartext = (f"(request-target): get {path}\nhost: {host}\ndate: {date}").encode(
        "utf-8"
    )
    signature = private_key.sign(cleartext, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(signature).decode("ascii")
    sig_header = (
        f'keyId="{key_id}",algorithm="{algorithm}",'
        f'headers="{headers_list}",signature="{sig_b64}"'
    )
    rf = RequestFactory()
    return rf.get(path, HTTP_HOST=host, HTTP_DATE=date, HTTP_SIGNATURE=sig_header)


def _generate_keypair() -> tuple[rsa.RSAPrivateKey, str]:
    """Return (private_key, public_pem) — a fresh RSA-2048 keypair."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_key, public_pem


@pytest.mark.django_db(databases="__all__")
class TestVerifyHttpSignature:
    """Exercise the verifier end-to-end with a real RSA keypair against a
    seeded ``takahe.Identity`` row."""

    @pytest.fixture(autouse=True)
    def setup_keys(self):
        from takahe.models import Domain, Identity

        self.private_key, public_pem = _generate_keypair()
        self.actor_uri = "https://remote.test/actor"
        self.key_id = self.actor_uri + "#main-key"
        domain, _created = Domain.objects.get_or_create(
            domain="remote.test", defaults={"local": False}
        )
        self.identity = Identity.objects.create(
            actor_uri=self.actor_uri,
            public_key=public_pem,
            local=False,
            username="signer",
            domain=domain,
        )

    def test_valid_signature_resolves_signer(self):
        from takahe.auth import verify_http_signature

        request = _make_signed_get(
            self.private_key, key_id=self.key_id, path="/collection/zzz"
        )
        result = verify_http_signature(request)
        assert result is not None
        assert result.pk == self.identity.pk

    def test_missing_signature_rejected(self):
        from takahe.auth import _SigError, verify_http_signature

        request = RequestFactory().get("/collection/zzz")
        with pytest.raises(_SigError):
            verify_http_signature(request)

    def test_unknown_algorithm_rejected(self):
        from takahe.auth import _SigError, verify_http_signature

        request = _make_signed_get(
            self.private_key,
            key_id=self.key_id,
            path="/collection/zzz",
            algorithm="hs2019",
        )
        with pytest.raises(_SigError):
            verify_http_signature(request)

    def test_wrong_headers_list_rejected(self):
        from takahe.auth import _SigError, verify_http_signature

        request = _make_signed_get(
            self.private_key,
            key_id=self.key_id,
            path="/collection/zzz",
            headers_list="(request-target) host",
        )
        with pytest.raises(_SigError):
            verify_http_signature(request)

    def test_stale_date_rejected(self):
        from takahe.auth import _SigError, verify_http_signature

        # 1 hour in the past — well outside the 300s skew window.
        old_date = formatdate(timeval=time.time() - 3600, usegmt=True)
        request = _make_signed_get(
            self.private_key,
            key_id=self.key_id,
            path="/collection/zzz",
            date=old_date,
        )
        with pytest.raises(_SigError):
            verify_http_signature(request)

    def test_unknown_signer_rejected(self):
        from takahe.auth import _SigError, verify_http_signature

        request = _make_signed_get(
            self.private_key,
            key_id="https://other.test/actor#main-key",
            path="/collection/zzz",
        )
        with pytest.raises(_SigError):
            verify_http_signature(request)

    def test_signature_mismatch_rejected(self):
        from takahe.auth import _SigError, verify_http_signature

        # Sign with one key but claim a different (registered) keyId.
        other_private, _ = _generate_keypair()
        request = _make_signed_get(
            other_private, key_id=self.key_id, path="/collection/zzz"
        )
        with pytest.raises(_SigError):
            verify_http_signature(request)


@pytest.mark.django_db(databases="__all__")
class TestItemGetByApObject:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book = Edition.objects.create(title="Local Book")

    def test_local_url_resolves(self):
        from catalog.models import Item

        result = Item.get_by_ap_object(
            {"href": self.book.absolute_url, "type": "Edition"}
        )
        assert result is not None
        assert result.pk == self.book.pk

    def test_unsupported_type_returns_none(self):
        from catalog.models import Item

        result = Item.get_by_ap_object({"href": "https://x/y", "type": "TVEpisode"})
        assert result is None

    def test_missing_href_returns_none(self):
        from catalog.models import Item

        assert Item.get_by_ap_object({"type": "Edition"}) is None
