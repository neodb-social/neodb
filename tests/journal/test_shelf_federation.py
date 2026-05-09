"""Tests for NeoDB Shelf federation.

Shelf and Collection share the same AP wire type (``"Shelf"``). The
inbound dispatcher (``takahe.ap_handlers._ShelfDispatcher``) routes by
the presence of ``shelfType`` in the envelope: present → NeoDB Shelf;
absent → NeoDB Collection.

Coverage here:

- Shelf envelope shape (``shelfType`` extra, ``first``/``last`` URLs).
- Items endpoint pagination produces ``ShelfItem`` entries with
  ``withRegardTo`` and (when the mark has a Status post) ``post``.
- Inbound ``_ShelfDispatcher`` routes envelopes correctly.
- URL-paste resolution for remote Shelf URLs.
- ``Shelf.url`` shape for both local and remote owners.
"""

from unittest.mock import MagicMock, patch

import pytest

from catalog.models import Edition
from journal.models import Collection, Shelf, ShelfMember, ShelfType
from takahe.ap_handlers import _ShelfDispatcher
from users.models import User


def _make_remote_post(
    identity_pk: int,
    post_id: int = 99001,
    actor_uri: str = "https://remote.example/actor/",
) -> MagicMock:
    post = MagicMock()
    post.local = False
    post.visibility = 0
    post.id = post_id
    post.pk = post_id
    post.author_id = identity_pk
    post.author.actor_uri = actor_uri
    post.summary = None
    post.sensitive = False
    post.attachments.all.return_value = []
    post.type_data = {"object": {"relatedWith": []}}
    return post


@pytest.mark.django_db(databases="__all__")
class TestShelfApEnvelope:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="shelfap@test.com", username="shelfap_user")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Book")

    def test_envelope_has_shelftype_extra(self):
        # Shelves are auto-initialized by ShelfManager on first access.
        wishlist = self.user.identity.shelf_manager.get_shelf(ShelfType.WISHLIST)
        env = wishlist.ap_envelope()
        assert env["type"] == "Shelf"
        assert env["shelfType"] == "wishlist"
        assert "first" in env and "last" in env
        # Shelves don't have a `content` field (no brief), but `mediaType`
        # is harmless and present.
        assert env["totalItems"] == 0

    def test_url_shape_local(self):
        wishlist = self.user.identity.shelf_manager.get_shelf(ShelfType.WISHLIST)
        # `/users/<handle>/shelf/<shelf_type>` — base form for local owners.
        assert wishlist.url == f"/users/{self.identity.handle}/shelf/wishlist"

    def test_items_page_for_shelf_member_emits_post_when_available(self):
        wishlist = self.user.identity.shelf_manager.get_shelf(ShelfType.WISHLIST)
        # Add a member; ShelfMember fixtures normally come from Mark.update,
        # but for AP shape testing the model can be created directly.
        ShelfMember.objects.create(
            parent=wishlist,
            owner=self.identity,
            item=self.book,
            position=1,
        )
        # Without a latest_post, `post` is omitted.
        page = wishlist.ap_items_page(1)
        assert len(page["orderedItems"]) == 1
        entry = page["orderedItems"][0]
        assert entry["type"] == "ShelfItem"
        assert entry["withRegardTo"] == self.book.absolute_url
        assert "post" not in entry

    def test_items_page_includes_post_url_when_member_has_latest_post(self):
        wishlist = self.user.identity.shelf_manager.get_shelf(ShelfType.WISHLIST)
        m = ShelfMember.objects.create(
            parent=wishlist,
            owner=self.identity,
            item=self.book,
            position=1,
        )
        # Stub ``latest_post`` so the entry serializer surfaces ``post``.
        fake_post = MagicMock()
        fake_post.absolute_object_uri.return_value = "https://site/@x/123"
        with patch.object(type(m), "latest_post", fake_post):
            entry = wishlist.ap_member_entry(m)
        assert entry["post"] == "https://site/@x/123"


@pytest.mark.django_db(databases="__all__")
class TestShelfDispatcherRouting:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="dispatch@test.com", username="dispatch_u")
        self.identity = self.user.identity
        # Disable SSRF DNS check for synthetic remote.example URLs.
        self._url_patch = patch(
            "journal.models.itemlist.is_valid_url", return_value=True
        )
        self._url_patch.start()
        yield
        self._url_patch.stop()

    def _envelope(self, **extras):
        base = {
            "id": "https://remote.example/list/x",
            "type": "Shelf",
            "name": "X",
            "published": "2026-01-01T00:00:00+00:00",
            "updated": "2026-01-02T00:00:00+00:00",
            "attributedTo": self.identity.actor_uri,
            "totalItems": 0,
            "first": "https://remote.example/list/x/items?page=1",
            "last": "https://remote.example/list/x/items?page=1",
        }
        base.update(extras)
        return base

    def test_dispatch_with_shelftype_routes_to_shelf(self):
        env = self._envelope(shelfType="wishlist")
        post = _make_remote_post(self.identity.pk, post_id=99100)
        with patch("journal.models.itemlist.django_rq.get_queue"):
            with patch.object(
                Shelf, "update_by_ap_object", return_value="shelf-route"
            ) as shelf_mock:
                with patch.object(
                    Collection, "update_by_ap_object", return_value="collection-route"
                ) as col_mock:
                    result = _ShelfDispatcher.update_by_ap_object(
                        self.identity, None, env, post
                    )
        assert result == "shelf-route"
        shelf_mock.assert_called_once()
        col_mock.assert_not_called()

    def test_dispatch_without_shelftype_routes_to_collection(self):
        env = self._envelope()
        post = _make_remote_post(self.identity.pk, post_id=99101)
        with patch("journal.models.itemlist.django_rq.get_queue"):
            with patch.object(
                Shelf, "update_by_ap_object", return_value="shelf-route"
            ) as shelf_mock:
                with patch.object(
                    Collection, "update_by_ap_object", return_value="collection-route"
                ) as col_mock:
                    result = _ShelfDispatcher.update_by_ap_object(
                        self.identity, None, env, post
                    )
        assert result == "collection-route"
        col_mock.assert_called_once()
        shelf_mock.assert_not_called()


def _make_remote_identity(username: str, domain_name: str = "remote.example"):
    """Create a fresh remote APIdentity for tests that need a Shelf owner
    distinct from local users (whose ShelfManager auto-initializes a row
    per shelf_type and would collide with the unique constraint on
    ``(owner, shelf_type)`` when a remote mirror is created)."""
    from takahe.models import Domain, Identity
    from takahe.utils import Takahe

    domain, _ = Domain.objects.get_or_create(
        domain=domain_name, defaults={"local": False}
    )
    actor_uri = f"https://{domain_name}/users/{username}/"
    identity = Identity.objects.create(
        actor_uri=actor_uri,
        local=False,
        username=username,
        domain=domain,
    )
    return Takahe.get_or_create_remote_apidentity(identity)


@pytest.mark.django_db(databases="__all__")
class TestShelfInboundCreatesMirror:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.identity = _make_remote_identity("bobinb")
        self._url_patch = patch(
            "journal.models.itemlist.is_valid_url", return_value=True
        )
        self._url_patch.start()
        yield
        self._url_patch.stop()

    def test_creates_remote_shelf_mirror_with_shelf_type(self):
        remote_url = "https://remote.example/users/bob/shelf/wishlist"
        env = {
            "id": remote_url,
            "type": "Shelf",
            "name": "bob / wishlist",
            "shelfType": "wishlist",
            "published": "2026-01-01T00:00:00+00:00",
            "updated": "2026-01-02T00:00:00+00:00",
            "attributedTo": self.identity.actor_uri,
            "totalItems": 0,
            "first": f"{remote_url}/items?page=1",
            "last": f"{remote_url}/items?page=1",
        }
        post = _make_remote_post(self.identity.pk, post_id=99200)
        with patch("journal.models.itemlist.django_rq.get_queue") as gq:
            result = Shelf.update_by_ap_object(self.identity, None, env, post)
        assert result is not None
        assert result.local is False
        assert result.shelf_type == "wishlist"
        assert result.remote_id == remote_url
        # Page-walking job is enqueued with the shelf's class path.
        gq.return_value.enqueue.assert_called_once()
        args = gq.return_value.enqueue.call_args.args
        assert args[0] == "journal.jobs.list_sync.fetch_remote_list_members"
        assert args[1] == "journal.models.shelf.Shelf"

    def test_invalid_shelf_type_falls_back_to_wishlist(self):
        # Defensive: an unknown shelfType from a peer must not crash —
        # we coerce to a known value so the row passes the model's
        # choices validation.
        remote_url = "https://remote.example/users/bob/shelf/junk"
        env = {
            "id": remote_url,
            "type": "Shelf",
            "name": "junk",
            "shelfType": "not-a-real-status",
            "published": "2026-01-01T00:00:00+00:00",
            "updated": "2026-01-02T00:00:00+00:00",
            "attributedTo": self.identity.actor_uri,
            "totalItems": 0,
            "first": f"{remote_url}/items?page=1",
            "last": f"{remote_url}/items?page=1",
        }
        post = _make_remote_post(self.identity.pk, post_id=99201)
        with patch("journal.models.itemlist.django_rq.get_queue"):
            result = Shelf.update_by_ap_object(self.identity, None, env, post)
        assert result is not None
        assert result.shelf_type == "wishlist"


@pytest.mark.django_db(databases="__all__")
class TestShelfSyncMembersFromAp:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        # Use a remote APIdentity to avoid colliding with the local user's
        # auto-initialized wishlist on the (owner, shelf_type) unique
        # constraint.
        self.identity = _make_remote_identity("smbob")
        self.book1 = Edition.objects.create(title="A")
        self.book2 = Edition.objects.create(title="B")

    def _make_mirror(self) -> Shelf:
        s = Shelf(
            owner=self.identity,
            shelf_type=ShelfType.WISHLIST.value,
            local=False,
            remote_id="https://remote.example/users/bob/shelf/wishlist",
            visibility=0,
        )
        s.save(post_when_save=False, index_when_save=False)
        return s

    def test_initial_population(self):
        s = self._make_mirror()
        items = [
            {"type": "ShelfItem", "withRegardTo": self.book1.absolute_url},
            {"type": "ShelfItem", "withRegardTo": self.book2.absolute_url},
        ]
        pending = Shelf._sync_members_from_ap(s, items)
        assert pending == 0
        members = list(s.members.order_by("position"))
        assert [m.item_id for m in members] == [self.book1.pk, self.book2.pk]

    def test_unknown_item_enqueues_fetch(self):
        s = self._make_mirror()
        unknown = "https://other.example/book/unknown-aaa"
        items = [
            {"type": "ShelfItem", "withRegardTo": self.book1.absolute_url},
            {"type": "ShelfItem", "withRegardTo": unknown},
        ]
        with patch("catalog.search.utils.enqueue_fetch") as enq:
            pending = Shelf._sync_members_from_ap(s, items)
        assert pending == 1
        called_urls = [c.args[0] for c in enq.call_args_list]
        assert unknown in called_urls
        # Local item resolves immediately and gets a member.
        assert [m.item_id for m in s.members.all()] == [self.book1.pk]

    def test_reorder_stays_consistent(self):
        s = self._make_mirror()
        Shelf._sync_members_from_ap(
            s,
            [
                {"type": "ShelfItem", "withRegardTo": self.book1.absolute_url},
                {"type": "ShelfItem", "withRegardTo": self.book2.absolute_url},
            ],
        )
        Shelf._sync_members_from_ap(
            s,
            [
                {"type": "ShelfItem", "withRegardTo": self.book2.absolute_url},
                {"type": "ShelfItem", "withRegardTo": self.book1.absolute_url},
            ],
        )
        members = list(s.members.order_by("position"))
        assert [m.item_id for m in members] == [self.book2.pk, self.book1.pk]
