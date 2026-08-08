import pytest

from catalog.models import Edition
from journal.models import Mark, Note, Review, ShelfMember, ShelfType
from journal.search import JournalIndex, JournalQueryParser
from takahe.models import Domain, Post
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestSearch:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.index = JournalIndex.instance()
        self.index.delete_all()
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="The Fall of Hyperion")
        self.book3 = Edition.objects.create(title="Andymion")
        self.book4 = Edition.objects.create(title="The Rise of Endymion")
        self.user1 = User.register(email="x@y.com", username="userx")
        self.user2 = User.register(email="a@b.com", username="usery")

    def test_search_post(self):
        # mark two books
        mark = Mark(self.user1.identity, self.book1)
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, ["Sci-Fi", "fic"], 0)
        mark = Mark(self.user1.identity, self.book2)
        mark.update(ShelfType.WISHLIST, "a gentle comment", None, ["nonfic"], 1)

        # search the marks by owner
        q = JournalQueryParser("gentle")
        q.filter_by_owner(self.user1.identity)
        r = self.index.search(q)
        assert r.total == 2

        # search the marks by visitor
        q = JournalQueryParser("gentle")
        q.filter_by_viewer(self.user2.identity)
        r = self.index.search(q)
        assert r.total == 1

        # update mark and search again
        mark = Mark(self.user1.identity, self.book1)
        mark.update(ShelfType.PROGRESS, "an updated comment", 9, ["Sci-Fi", "fic"], 0)

        # search the marks
        q = JournalQueryParser("gentle")
        q.filter_by_owner(self.user1.identity)
        r = self.index.search(q)
        assert r.total == 1
        assert r.posts[0].state == "new"

        # delete the other mark
        mark = Mark(self.user1.identity, self.book2)
        mark.delete()

        # search the marks
        q = JournalQueryParser("gentle")
        q.filter_by_owner(self.user1.identity)
        r = self.index.search(q)
        assert r.total == 0

    def test_search_post_visibility_for_viewer(self):
        mark = Mark(self.user1.identity, self.book1)
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, ["Sci-Fi"], 0)
        mark = Mark(self.user1.identity, self.book2)
        mark.update(ShelfType.WISHLIST, "a gentle comment", None, ["nonfic"], 1)
        mark = Mark(self.user1.identity, self.book3)
        mark.update(ShelfType.WISHLIST, "a gentle comment", None, ["private"], 2)

        q = JournalQueryParser("gentle")
        q.filter_by_viewer(self.user2.identity)
        r = self.index.search(q)
        assert r.total == 1

        self.user2.identity.follow(self.user1.identity, True)
        q = JournalQueryParser("gentle")
        q.filter_by_viewer(self.user2.identity)
        r = self.index.search(q)
        assert r.total == 2

        q = JournalQueryParser("gentle")
        q.filter_by_viewer(self.user1.identity)
        r = self.index.search(q)
        assert r.total == 3


def _make_remote_identity(username: str, domain_name: str = "remote.example"):
    domain, _ = Domain.objects.get_or_create(
        domain=domain_name, defaults={"local": False}
    )
    identity = TakaheIdentity.objects.create(
        actor_uri=f"https://{domain_name}/users/{username}/",
        local=False,
        username=username,
        domain=domain,
    )
    return Takahe.get_or_create_remote_apidentity(identity)


def _make_remote_post(owner_pk: int, uri: str, obj: dict) -> Post:
    return Post.objects.create(
        author_id=owner_pk,
        local=False,
        object_uri=uri,
        content=obj.get("content", ""),
        type="Article" if obj["type"] == "Review" else "Note",
        type_data={"object": {"relatedWith": [obj]}},
        visibility=Post.Visibilities.public,
        state="fanned_out",
    )


@pytest.mark.django_db(databases="__all__")
class TestRemotePieceIndex:
    """Regression tests for #1761: docs indexed for remote pieces must
    reference the linked post, so that item post search returns as many
    posts as it counts."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.index = JournalIndex.instance()
        self.index.delete_all()
        self.book = Edition.objects.create(title="Hyperion")
        self.owner = _make_remote_identity("reader")

    def search_item_posts(self, piece_type: str):
        # mirrors the query built by /api/item/{uuid}/posts/
        q = JournalQueryParser(f"type:{piece_type}", 1)
        q.filter_by_viewer(None)
        q.filter("item_id", self.book.pk)
        q.filter("post_id", ">0")
        q.sort(["created:desc"])
        return self.index.search(q)

    def test_remote_review_count_matches_posts(self):
        obj = {
            "id": "https://remote.example/review/1",
            "type": "Review",
            "name": "Great Book",
            "content": "review body",
            "mediaType": "text/markdown",
            "published": "2026-01-01T00:00:00+00:00",
        }
        post = _make_remote_post(self.owner.pk, obj["id"], obj)
        piece = Review.update_by_ap_object(self.owner, self.book, obj, post)
        assert piece is not None
        assert piece.latest_post_id == post.pk
        r = self.search_item_posts("review")
        posts = list(r.posts)
        assert r.total == len(posts) == 1
        assert posts[0].pk == post.pk

    def test_remote_review_replacement_post(self):
        # remote user deleted and recreated their review post; the newer
        # object arrives linked to a different post
        obj = {
            "id": "https://remote.example/review/1",
            "type": "Review",
            "name": "Great Book",
            "content": "review body",
            "mediaType": "text/markdown",
            "published": "2026-01-01T00:00:00+00:00",
        }
        post1 = _make_remote_post(self.owner.pk, obj["id"], obj)
        Review.update_by_ap_object(self.owner, self.book, obj, post1)
        post1.state = "deleted"
        post1.save()
        obj2 = {
            **obj,
            "id": "https://remote.example/review/2",
            "content": "updated body",
            "updated": "2026-01-02T00:00:00+00:00",
        }
        post2 = _make_remote_post(self.owner.pk, obj2["id"], obj2)
        piece = Review.update_by_ap_object(self.owner, self.book, obj2, post2)
        assert piece is not None
        assert piece.latest_post_id == post2.pk
        r = self.search_item_posts("review")
        posts = list(r.posts)
        assert r.total == len(posts) == 1
        assert posts[0].pk == post2.pk

    def test_remote_review_refetched_post(self):
        # takahe pruned the remote post; a refetch of the same unchanged
        # object recreates it under a new pk, which must be relinked
        obj = {
            "id": "https://remote.example/review/1",
            "type": "Review",
            "name": "Great Book",
            "content": "review body",
            "mediaType": "text/markdown",
            "published": "2026-01-01T00:00:00+00:00",
        }
        post1 = _make_remote_post(self.owner.pk, obj["id"], obj)
        Review.update_by_ap_object(self.owner, self.book, obj, post1)
        post1.delete()
        post2 = _make_remote_post(self.owner.pk, obj["id"], obj)
        piece = Review.update_by_ap_object(self.owner, self.book, obj, post2)
        assert piece is not None
        assert piece.latest_post_id == post2.pk
        assert Review.objects.filter(owner=self.owner, item=self.book).count() == 1
        r = self.search_item_posts("review")
        posts = list(r.posts)
        assert r.total == len(posts) == 1
        assert posts[0].pk == post2.pk

    def test_remote_stale_refetch_does_not_displace_live_post(self):
        # an old pruned post refetched under a new pk must not become the
        # piece's latest post while a newer post is still live
        obj = {
            "id": "https://remote.example/review/1",
            "type": "Review",
            "name": "Great Book",
            "content": "review body",
            "mediaType": "text/markdown",
            "published": "2026-01-01T00:00:00+00:00",
        }
        post1 = _make_remote_post(self.owner.pk, obj["id"], obj)
        Review.update_by_ap_object(self.owner, self.book, obj, post1)
        post1.delete()
        obj2 = {
            **obj,
            "id": "https://remote.example/review/2",
            "content": "updated body",
            "updated": "2026-01-02T00:00:00+00:00",
        }
        post2 = _make_remote_post(self.owner.pk, obj2["id"], obj2)
        Review.update_by_ap_object(self.owner, self.book, obj2, post2)
        post3 = _make_remote_post(self.owner.pk, obj["id"], obj)
        piece = Review.update_by_ap_object(self.owner, self.book, obj, post3)
        assert piece is not None
        assert piece.latest_post_id == post2.pk
        r = self.search_item_posts("review")
        posts = list(r.posts)
        assert r.total == len(posts) == 1
        assert posts[0].pk == post2.pk

    def test_remote_note_refetched_post(self):
        # same as above for notes, which additionally must not be
        # duplicated when the piece can only be matched by remote_id
        obj = {
            "id": "https://remote.example/note/1",
            "type": "Note",
            "content": "note content",
            "published": "2026-01-01T00:00:00+00:00",
        }
        post1 = _make_remote_post(self.owner.pk, obj["id"], obj)
        Note.update_by_ap_object(self.owner, self.book, obj, post1)
        post1.delete()
        post2 = _make_remote_post(self.owner.pk, obj["id"], obj)
        piece = Note.update_by_ap_object(self.owner, self.book, obj, post2)
        assert piece is not None
        assert piece.latest_post_id == post2.pk
        assert Note.objects.filter(owner=self.owner, item=self.book).count() == 1
        r = self.search_item_posts("note")
        posts = list(r.posts)
        assert r.total == len(posts) == 1
        assert posts[0].pk == post2.pk

    def test_remote_note_count_matches_posts(self):
        obj = {
            "id": "https://remote.example/note/1",
            "type": "Note",
            "content": "note content",
            "published": "2026-01-01T00:00:00+00:00",
            "progress": {"type": "page", "value": "42"},
        }
        post = _make_remote_post(self.owner.pk, obj["id"], obj)
        piece = Note.update_by_ap_object(self.owner, self.book, obj, post)
        assert piece is not None
        assert piece.latest_post_id == post.pk
        r = self.search_item_posts("note")
        posts = list(r.posts)
        assert r.total == len(posts) == 1
        assert posts[0].pk == post.pk


@pytest.mark.django_db(databases="__all__")
class TestLocalPostDeleted:
    """A local mark whose post is deleted from a Mastodon client is kept,
    but its index doc must stop counting toward item post listings."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.index = JournalIndex.instance()
        self.index.delete_all()
        self.book = Edition.objects.create(title="Hyperion")
        self.user = User.register(email="pd@y.com", username="pduser")
        self.identity = self.user.identity

    def test_deleted_post_stops_counting(self):
        from takahe.ap_handlers import post_deleted

        mark = Mark(self.identity, self.book)
        mark.update(ShelfType.COMPLETE, "a fine comment", 8, [], 0)
        member = ShelfMember.objects.get(owner=self.identity, item=self.book)
        post_pk = member.latest_post_id
        assert post_pk is not None
        Takahe.delete_posts([post_pk])
        post_deleted(post_pk, True, None)
        # the piece is kept and still searchable by its owner
        member.refresh_from_db()
        q = JournalQueryParser("fine")
        q.filter_by_owner(self.identity)
        assert self.index.search(q).total == 1
        # but item post listings no longer count it
        q = JournalQueryParser("type:mark", 1)
        q.filter_by_viewer(None)
        q.filter("item_id", self.book.pk)
        q.filter("post_id", ">0")
        r = self.index.search(q)
        assert r.total == len(list(r.posts)) == 0
