"""Tests for Collection federation across servers.

Covers the two-step model after the AP wire-type rename to ``Shelf``:

- ``Collection.ap_object`` (embedded in the announcement Note Post) carries
  the lightweight Shelf envelope: ``first``/``last`` URLs pointing at the
  paginated items endpoint, no ``orderedItems`` inline.
- ``/collection/<uuid>/items`` (no ``page``) returns the AS-standard
  ``OrderedCollection`` envelope.
- ``/collection/<uuid>/items?page=N`` returns one
  ``OrderedCollectionPage`` slice (``ShelfItem`` entries with
  ``withRegardTo`` + optional ``commentText``).
- ``Collection.update_by_ap_object`` (delegating to the shared
  ``List.update_by_ap_envelope``) creates / updates the local mirror
  from the lightweight Note payload and schedules a paginated
  member-fetch job.
- ``Collection._sync_members_from_ap`` upserts members atomically when
  given a flattened items list (called from the page-walking job after
  it follows ``first``→``next``).
- URL paste resolution maps a remote Collection URL to the local mirror
  while respecting visibility, and now also handles remote Shelf URLs.
- ``collection_retrieve`` is HTML-only: AP peers consume the Shelf
  envelope inline from the announcement Post's ``relatedWith[0]`` (the
  pattern Review uses); the ``_list_ap_object_view`` helper is still
  exercised here because ``shelf_ap_retrieve`` calls it.

Gaps (not covered here, called out for future work):
- ``takahe.auth.sign_get`` outbound signing (needs httpx + key fixtures).
- ``fetch_remote_list_members`` page-walker job execution end-to-end
  (needs httpx mock).
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
    # ``List.update_by_ap_envelope`` matches the inbound ``id`` URL host
    # against the announcing author's actor URL host, so this must be a
    # real string (rather than the MagicMock proxy default) and share
    # the host used by the synthetic ``remote.example`` URLs the tests
    # pass in.
    post.author.actor_uri = actor_uri
    post.summary = None
    post.sensitive = False
    post.attachments.all.return_value = []
    post.type_data = {"object": {"relatedWith": []}}
    return post


def _items(*books_with_notes):
    out = []
    for book, note in books_with_notes:
        entry = {
            "type": "ShelfItem",
            "withRegardTo": book.absolute_url,
        }
        if note:
            entry["commentText"] = note
        out.append(entry)
    return out


def _shelf_envelope(remote_url: str, owner, **overrides) -> dict:
    """A lightweight Shelf AP object as it would arrive embedded in a
    Note Post — no ``orderedItems``, only ``first``/``last`` links."""
    base = {
        "id": remote_url,
        "type": "Shelf",
        "name": "Remote",
        "content": "",
        "mediaType": "text/markdown",
        "published": "2026-01-01T00:00:00+00:00",
        "updated": "2026-01-02T00:00:00+00:00",
        "attributedTo": owner.actor_uri,
        "href": remote_url,
        "totalItems": 0,
        "first": f"{remote_url}/items?page=1",
        "last": f"{remote_url}/items?page=1",
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

    def test_ap_object_is_lightweight_shelf_envelope(self):
        c = Collection.objects.create(
            owner=self.identity, title="Reading list", visibility=0
        )
        c.append_item(self.book1, note="first")
        c.append_item(self.book2, note="second")
        obj = c.ap_object
        assert obj["type"] == "Shelf"
        assert obj["name"] == "Reading list"
        assert obj["totalItems"] == 2
        assert "orderedItems" not in obj
        assert obj["first"].endswith("/items?page=1")
        assert obj["last"].endswith("/items?page=1")
        assert obj["id"] == c.absolute_url

    def test_items_envelope_no_page(self):
        c = Collection.objects.create(owner=self.identity, title="L", visibility=0)
        c.append_item(self.book1, note="x")
        env = c.ap_items_envelope()
        assert env["type"] == "OrderedCollection"
        assert env["totalItems"] == 1
        assert env["id"] == c.absolute_url + "/items"
        assert env["first"].endswith("/items?page=1")
        assert env["last"].endswith("/items?page=1")

    def test_items_page_shape(self):
        c = Collection.objects.create(owner=self.identity, title="L", visibility=0)
        c.append_item(self.book1, note="hello")
        c.append_item(self.book2, note=None)
        page = c.ap_items_page(1)
        assert page["type"] == "OrderedCollectionPage"
        assert page["partOf"] == c.absolute_url + "/items"
        items = page["orderedItems"]
        assert len(items) == 2
        assert all(it["type"] == "ShelfItem" for it in items)
        assert items[0]["withRegardTo"] == self.book1.absolute_url
        assert items[0]["commentText"] == "hello"
        # `commentText` omitted (not empty-string) when no note set.
        assert "commentText" not in items[1]
        # Single page → no next/prev.
        assert "next" not in page
        assert "prev" not in page

    def test_items_page_pagination_links_for_two_pages(self):
        # Force a second page by stubbing the page size constant inside
        # the shared envelope helpers — cheaper than creating 100+ items.
        from journal.models import itemlist as itemlist_mod

        c = Collection.objects.create(owner=self.identity, title="L", visibility=0)
        c.append_item(self.book1, note="x")
        c.append_item(self.book2, note="y")
        with patch.object(itemlist_mod, "AP_PAGE_SIZE", 1):
            env = c.ap_items_envelope()
            assert env["totalItems"] == 2
            assert env["last"].endswith("/items?page=2")
            page1 = c.ap_items_page(1)
            assert "next" in page1
            assert page1["next"].endswith("/items?page=2")
            assert "prev" not in page1
            page2 = c.ap_items_page(2)
            assert "prev" in page2
            assert page2["prev"].endswith("/items?page=1")
            assert "next" not in page2

    def test_items_page_out_of_range_returns_empty(self):
        c = Collection.objects.create(owner=self.identity, title="L", visibility=0)
        c.append_item(self.book1, note="x")
        page = c.ap_items_page(99)
        assert page["orderedItems"] == []


@pytest.mark.django_db(databases="__all__")
class TestCollectionUpdateByApObject:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="colsync@test.com", username="colsync_user")
        self.identity = self.user.identity
        # ``is_valid_url`` does DNS resolution; the synthetic
        # ``remote.example`` hosts in this fixture won't resolve in the
        # CI sandbox. The tests exercise federation logic, not the SSRF
        # gate, so the gate is short-circuited at the import site used by
        # ``List.update_by_ap_envelope``.
        self._url_patch = patch(
            "journal.models.itemlist.is_valid_url", return_value=True
        )
        self._url_patch.start()
        yield
        self._url_patch.stop()

    def test_create_mirror_enqueues_member_fetch(self):
        remote_url = "https://remote.example/collection/abc123abc123abc123abc1"
        ap_obj = _shelf_envelope(remote_url, self.identity, name="Remote Picks")
        post = _make_remote_post(self.identity.pk)
        with patch("journal.models.itemlist.django_rq.get_queue") as gq:
            result = Collection.update_by_ap_object(self.identity, None, ap_obj, post)
            assert result is not None
            assert result.local is False
            assert result.remote_id == remote_url
            assert result.title == "Remote Picks"
            # The local catalog gate must NOT auto-create a CatalogCollection
            # for remote mirrors (regression guard for sentry MEDIUM finding).
            assert result.catalog_item_id is None
            # Member fetch is scheduled asynchronously; the Note carries no items.
            gq.return_value.enqueue.assert_called_once()
            args = gq.return_value.enqueue.call_args.args
            assert args[0] == "journal.jobs.list_sync.fetch_remote_list_members"
            # First arg is the dotted class path so the job can resolve
            # both Collection and Shelf models from one entry point.
            assert args[1] == "journal.models.collection.Collection"
            assert args[2] == result.pk
            # ``items_url`` is forwarded from the envelope so the job
            # doesn't have to re-dereference ``remote_id``.
            assert args[3] == ap_obj["first"]
            # No inline ``orderedItems`` in this envelope.
            assert args[4] is None
        assert result.members.count() == 0

    def test_stale_payload_is_ignored(self):
        remote_url = "https://remote.example/collection/old"
        ap_obj = _shelf_envelope(remote_url, self.identity, name="Stable")
        post = _make_remote_post(self.identity.pk, post_id=91003)
        with patch("journal.models.itemlist.django_rq.get_queue"):
            col = Collection.update_by_ap_object(self.identity, None, ap_obj, post)
        assert col is not None
        original_edited = col.edited_time
        stale = dict(ap_obj)
        stale["updated"] = "2025-01-01T00:00:00+00:00"
        stale["name"] = "Should not apply"
        with patch("journal.models.itemlist.django_rq.get_queue"):
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
                "type": "ShelfItem",
                "withRegardTo": self.book1.absolute_url,
                "commentText": "known",
            },
            {
                "type": "ShelfItem",
                "withRegardTo": unknown_item_url,
                "commentText": "unknown",
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
            "journal.models.itemlist.is_valid_url", return_value=True
        )
        self._url_patch.start()
        yield
        self._url_patch.stop()

    def test_resolve_url_returns_local_mirror(self):
        remote_url = "https://remote.example/collection/aaaaaaaaaaaaaaaaaaaaaa"
        ap_obj = _shelf_envelope(remote_url, self.identity, name="Remote")
        post = _make_remote_post(self.identity.pk, post_id=92001)
        with patch("journal.models.itemlist.django_rq.get_queue"):
            Collection.update_by_ap_object(self.identity, None, ap_obj, post)
        col = Collection.objects.get(remote_id=remote_url)
        rf = RequestFactory().get("/search?q=" + remote_url)
        rf.user = self.user
        # ``_list_sync_args_from_post`` reads from a real Takahē Post's
        # cached ``type_data``; the test fixture's ``MagicMock`` post
        # isn't persisted, so stub the helper to return the envelope
        # the announcement Post would actually carry.
        items_url = ap_obj["first"]
        with (
            patch("django_rq.get_queue") as gq,
            patch(
                "catalog.views.search._list_sync_args_from_post",
                return_value=(items_url, None),
            ),
        ):
            response = resolve_url_query(rf, remote_url)
        assert response is not None
        assert response.status_code in (302, 301)
        assert col.url in response["Location"]
        gq.return_value.enqueue.assert_called()
        args = gq.return_value.enqueue.call_args.args
        assert args[3] == items_url
        assert args[4] is None

    def test_resolve_url_skips_enqueue_when_cache_empty(self):
        # ``_list_sync_args_from_post`` returns ``(None, None)`` when
        # the announcement Post is missing or malformed. Skip the
        # enqueue rather than scheduling a no-op job — the user still
        # gets the existing mirror, and a refresh arrives with the
        # next pushed Update activity from the origin.
        remote_url = "https://remote.example/collection/cccccccccccccccccccccc"
        ap_obj = _shelf_envelope(remote_url, self.identity, name="NoCache")
        post = _make_remote_post(self.identity.pk, post_id=92003)
        with patch("journal.models.itemlist.django_rq.get_queue"):
            Collection.update_by_ap_object(self.identity, None, ap_obj, post)
        col = Collection.objects.get(remote_id=remote_url)
        rf = RequestFactory().get("/search?q=" + remote_url)
        rf.user = self.user
        with (
            patch("django_rq.get_queue") as gq,
            patch(
                "catalog.views.search._list_sync_args_from_post",
                return_value=(None, None),
            ),
        ):
            response = resolve_url_query(rf, remote_url)
        assert response is not None
        assert response.status_code in (302, 301)
        assert col.url in response["Location"]
        assert not gq.return_value.enqueue.called

    def test_resolve_url_hides_invisible_remote_mirror(self):
        remote_url = "https://remote.example/collection/bbbbbbbbbbbbbbbbbbbbbb"
        ap_obj = _shelf_envelope(remote_url, self.identity, name="Hidden")
        post = _make_remote_post(self.identity.pk, post_id=92002)
        post.visibility = 2  # Takahe Followers -> NeoDB visibility=1
        with patch("journal.models.itemlist.django_rq.get_queue"):
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
class TestApEndpointHelpers:
    """``collection_retrieve`` is HTML-only; peers consume the Shelf
    envelope inline from the announcement Post's ``relatedWith[0]``. The
    ``_list_ap_object_view`` / ``_list_items_view`` helpers are still
    exercised by ``shelf_ap`` routes and by the items-page sub-URL, so
    their behaviour is asserted directly with a ``RequestFactory``-built
    request."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="cn@test.com", username="cn_user")
        self.identity = self.user.identity
        self.collection = Collection.objects.create(
            owner=self.identity, title="Public", visibility=0
        )

    def test_ap_view_unsigned_public_returns_200(self):
        # Public collections are returned to anonymous callers — same as
        # the HTML view. This lets first-time peers and SystemActor-signed
        # GETs (whose actor we may not have cached) dereference the
        # collection AP without first having to verify their identity.
        from journal.views.collection import _list_ap_object_view

        rf = RequestFactory().get(self.collection.url)
        response = _list_ap_object_view(rf, self.collection)
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/activity+json")

    def test_ap_view_unsigned_followers_only_returns_404(self):
        # Followers-only and private collections require a signed GET from
        # an authorized follower; an unsigned probe gets 404 (not 403) so
        # existence is not leaked.
        from journal.views.collection import _list_ap_object_view

        c = Collection.objects.create(owner=self.identity, title="Hidden", visibility=1)
        rf = RequestFactory().get(c.url)
        response = _list_ap_object_view(rf, c)
        assert response.status_code == 404

    def test_items_endpoint_unsigned_public_returns_envelope(self):
        from journal.views.collection import _list_items_view

        rf = RequestFactory().get(self.collection.url + "/items")
        response = _list_items_view(rf, self.collection)
        assert response.status_code == 200
        import json

        body = json.loads(response.content)
        assert body["type"] == "OrderedCollection"
        assert "first" in body and "last" in body

    def test_items_endpoint_with_page_returns_page(self):
        from journal.views.collection import _list_items_view

        rf = RequestFactory().get(self.collection.url + "/items?page=1")
        response = _list_items_view(rf, self.collection)
        assert response.status_code == 200
        import json

        body = json.loads(response.content)
        assert body["type"] == "OrderedCollectionPage"
        assert body["partOf"].endswith("/items")
        assert body["orderedItems"] == []

    def test_items_endpoint_bad_page_returns_400(self):
        from journal.views.collection import _list_items_view

        rf = RequestFactory().get(self.collection.url + "/items?page=abc")
        response = _list_items_view(rf, self.collection)
        assert response.status_code == 400


@pytest.mark.django_db(databases="__all__")
class TestApViewRefusesRemote:
    """AP endpoints must never serve a mirror's content — the origin
    server is authoritative for their own users' lists. We only expose
    AP for lists that are local AND owned by a local APIdentity.
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="apremote@test.com", username="apremote_u")
        self.identity = self.user.identity
        self._url_patch = patch(
            "journal.models.itemlist.is_valid_url", return_value=True
        )
        self._url_patch.start()
        yield
        self._url_patch.stop()

    def test_envelope_view_returns_404_for_local_mirror(self):
        from journal.views.collection import _list_ap_object_view

        col = Collection(
            owner=self.identity,
            local=False,
            remote_id="https://remote.example/collection/zzz",
            title="Mirror",
            visibility=0,
        )
        col.save(post_when_save=False, index_when_save=False)
        rf = RequestFactory().get(col.url)
        response = _list_ap_object_view(rf, col)
        assert response.status_code == 404

    def test_items_view_returns_404_for_local_mirror(self):
        from journal.views.collection import _list_items_view

        col = Collection(
            owner=self.identity,
            local=False,
            remote_id="https://remote.example/collection/yyy",
            title="Mirror",
            visibility=0,
        )
        col.save(post_when_save=False, index_when_save=False)
        rf = RequestFactory().get(col.url + "/items")
        response = _list_items_view(rf, col)
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


@pytest.mark.django_db(databases="__all__")
class TestIsVisibleToIdentityNone:
    """Regression coverage for the ``is_visible_to`` direct user-pk
    shortcut — owners must see their own content even when
    ``user.identity`` is unpopulated (rare: identity deletion,
    mid-signup)."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="vis@test.com", username="vis_user")
        self.collection = Collection.objects.create(
            owner=self.user.identity, title="Mine", visibility=2
        )

    def test_owner_sees_own_private_when_identity_dropped(self):
        # Simulate a User that lost its APIdentity link. The mixin must
        # still recognize the user as the owner via the user-pk shortcut,
        # not fall through to anonymous-viewer rules.
        with patch.object(type(self.user), "identity", None):
            assert self.collection.is_visible_to(self.user) is True

    def test_anonymous_does_not_see_private(self):
        assert self.collection.is_visible_to(None) is False


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


@pytest.mark.django_db(databases="__all__")
class TestFetchRemoteListMembersJob:
    """Exercise the two backward-compat / edge-case branches in
    ``journal.jobs.list_sync.fetch_remote_list_members`` directly:

    - Jobs queued under the previous signature
      ``(class_path, pk, attempts)`` must not crash — the integer in
      slot 3 is the retry counter, not an items URL.
    - An envelope that explicitly inlines an empty ``orderedItems``
      list (``[]``, no pagination) must still flow through
      ``_sync_members_from_ap`` so a peer can clear the mirror.
    - When the caller passes neither inline items nor a URL, the job
      is a no-op (avoids accidental member purges).
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="jobedge@test.com", username="jobedge_user")
        self.identity = self.user.identity
        self.collection = Collection.objects.create(
            owner=self.identity,
            title="JobEdge",
            visibility=0,
            local=False,
            remote_id="https://remote.example/collection/jobedge",
        )

    def test_legacy_int_in_items_url_slot_recovers_attempts(self):
        from journal.jobs.list_sync import fetch_remote_list_members

        with patch.object(Collection, "_sync_members_from_ap") as sync:
            # Old enqueue shape: (class_path, pk, attempts). The ``1``
            # in the items_url slot is a retry counter; it must not be
            # parsed as a URL or fed to the SSRF gate.
            fetch_remote_list_members(
                "journal.models.collection.Collection",
                self.collection.pk,
                1,
            )
            sync.assert_not_called()

    def test_empty_inline_items_clears_mirror(self):
        from journal.jobs.list_sync import fetch_remote_list_members

        with patch.object(Collection, "_sync_members_from_ap") as sync:
            sync.return_value = []
            fetch_remote_list_members(
                "journal.models.collection.Collection",
                self.collection.pk,
                None,
                [],
            )
            sync.assert_called_once()
            # The envelope explicitly carries an empty list — the job
            # must hand that through, not short-circuit on "no data".
            args, _ = sync.call_args
            assert args[1] == []

    def test_no_data_is_noop(self):
        from journal.jobs.list_sync import fetch_remote_list_members

        with patch.object(Collection, "_sync_members_from_ap") as sync:
            fetch_remote_list_members(
                "journal.models.collection.Collection",
                self.collection.pk,
                None,
                None,
            )
            sync.assert_not_called()
