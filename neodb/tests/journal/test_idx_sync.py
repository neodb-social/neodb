from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from catalog.models import Edition
from common.search.index import TYPESENSE_ERRORS
from journal.management.commands import journal as journal_command
from journal.models import Comment, Mark, Review, ShelfType
from journal.search import JournalIndex, JournalQueryParser
from takahe.models import Domain, Post
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestIdxSync:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.index = JournalIndex.instance()
        self.index.delete_all()
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.user1 = User.register(email="x@y.com", username="userx")
        self.user2 = User.register(email="a@b.com", username="usery")
        self.identity1 = self.user1.identity
        self.identity2 = self.user2.identity
        mark = Mark(self.identity1, self.book1)
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, ["Sci-Fi"], 0)
        mark = Mark(self.identity2, self.book2)
        mark.update(ShelfType.COMPLETE, "another comment", 8, ["fic"], 1)

    def run_sync(self, *args) -> str:
        out = StringIO()
        call_command("journal", "idx-sync", *args, stdout=out)
        return out.getvalue()

    def doc_ids(self, owner_id: int) -> set[str]:
        ids = self.index.get_doc_ids_by_owner(owner_id)
        assert ids is not None
        return ids

    def stale_docs(self, owner_id: int) -> list[dict]:
        return [
            {
                "id": "99999999",
                "post_id": [99999999],
                "piece_class": ["Post"],
                "content": ["stale post doc"],
                "created": 1700000000,
                "owner_id": owner_id,
                "visibility": 0,
            },
            {
                "id": "p99999999",
                "piece_id": [99999999],
                "piece_class": ["Comment"],
                "content": ["stale piece doc"],
                "created": 1700000000,
                "owner_id": owner_id,
                "visibility": 0,
            },
        ]

    def test_noop_when_in_sync(self):
        before = self.doc_ids(self.identity1.pk)
        assert before
        output = self.run_sync()
        assert "0 docs added, 0 docs deleted" in output
        assert "0 docs purged" in output
        assert self.doc_ids(self.identity1.pk) == before

    def test_add_missing_docs(self):
        # a review without post is indexed on save as a piece doc
        review = Review(
            owner=self.identity1,
            item=self.book1,
            title="my review",
            body="review body",
        )
        review.save(post_when_save=False)
        before = self.doc_ids(self.identity1.pk)
        assert f"p{review.pk}" in before
        # docs wiped from index are restored by sync, as both post and piece docs
        self.index.delete_by_owner([self.identity1.pk])
        assert self.doc_ids(self.identity1.pk) == set()
        self.run_sync()
        assert self.doc_ids(self.identity1.pk) == before

    def test_delete_stale_docs(self):
        before = self.doc_ids(self.identity1.pk)
        assert self.index.insert_docs(self.stale_docs(self.identity1.pk)) == 2
        self.run_sync()
        assert self.doc_ids(self.identity1.pk) == before

    def test_purge_deactivated_identity(self):
        assert self.doc_ids(self.identity2.pk)
        self.user2.is_active = False
        self.user2.save()
        output = self.run_sync()
        assert "1 deactivated identities" in output
        assert self.doc_ids(self.identity2.pk) == set()
        assert self.doc_ids(self.identity1.pk)

    def test_dry_run(self):
        assert self.index.insert_docs(self.stale_docs(self.identity1.pk)) == 2
        self.user2.is_active = False
        self.user2.save()
        before1 = self.doc_ids(self.identity1.pk)
        before2 = self.doc_ids(self.identity2.pk)
        assert before2
        output = self.run_sync("--dry-run")
        assert "would be" in output
        assert self.doc_ids(self.identity1.pk) == before1
        assert self.doc_ids(self.identity2.pk) == before2

    def test_owner_scope(self):
        self.index.delete_by_owner([self.identity1.pk, self.identity2.pk])
        self.run_sync("--owner", "userx")
        assert self.doc_ids(self.identity1.pk)
        assert self.doc_ids(self.identity2.pk) == set()

    def test_after_id_limit_slices_run(self):
        first, second = sorted([self.identity1.pk, self.identity2.pk])
        self.index.delete_by_owner([first, second])
        output = self.run_sync("--limit", "1")
        assert "syncing 1 of 2 candidate identities with id > 0" in output
        assert f"--after-id {first}" in output
        assert self.doc_ids(first)
        assert self.doc_ids(second) == set()
        self.run_sync("--after-id", str(first), "--limit", "1")
        assert self.doc_ids(second)

    def test_cursor_survives_shrinking_candidate_list(self):
        # the cursor is an id, not a position, so an identity dropping out of
        # the candidate list between slices must not shift the next slice past
        # an identity that was never synced
        first, second = sorted([self.identity1.pk, self.identity2.pk])
        self.index.delete_by_owner([first, second])
        self.run_sync("--limit", "1")
        user = User.objects.get(identity=first)
        user.is_active = False
        user.save()
        # first is gone from the candidate list now; a positional offset of 1
        # would land past second and skip it
        self.run_sync("--after-id", str(first), "--limit", "1")
        assert self.doc_ids(second)

    def test_resume_cursor_stops_before_failed_identity(self, monkeypatch):
        # advancing the cursor past an identity that errored would drop that
        # owner from every later slice and leave it unreconciled for good
        first, second = sorted([self.identity1.pk, self.identity2.pk])
        monkeypatch.setattr(
            JournalIndex,
            "get_doc_ids_by_owner",
            lambda self, owner_id: None if owner_id == first else set(),
        )
        output = self.run_sync("--limit", "2")
        assert f"--after-id {first}" not in output
        assert "no resume cursor" in output
        assert "1 identities skipped due to index errors" in output

    def test_resume_cursor_reports_partial_progress(self, monkeypatch):
        first, second = sorted([self.identity1.pk, self.identity2.pk])
        monkeypatch.setattr(
            JournalIndex,
            "get_doc_ids_by_owner",
            lambda self, owner_id: None if owner_id == second else set(),
        )
        output = self.run_sync("--limit", "2")
        assert f"--after-id {first}" in output
        assert "stops before the first identity that errored" in output

    def test_failed_write_blocks_resume_cursor(self, monkeypatch):
        # replace_docs() logs Typesense errors and returns a count, so a short
        # count is the only sign the write did not land; the cursor must not
        # move past an owner whose docs never made it in
        self.index.delete_by_owner([self.identity1.pk, self.identity2.pk])
        monkeypatch.setattr(JournalIndex, "replace_docs", lambda self, docs: 0)
        output = self.run_sync("--limit", "2")
        assert "no resume cursor" in output
        assert "2 identities skipped due to index errors" in output

    def test_export_docs_raises_on_error_response(self):
        # export_docs is a generator, so the request is only made once it is
        # iterated; a rejected export must surface as a TYPESENSE_ERRORS member
        # so get_doc_ids_by_owner() can turn it into None rather than an
        # empty-and-therefore-"delete-everything" result
        with pytest.raises(TYPESENSE_ERRORS):
            list(self.index.export_docs({"filter_by": "no_such_field:1"}))

    def test_negative_slice_values_rejected(self):
        for arg in ("--limit", "--after-id", "--throttle"):
            with pytest.raises(CommandError):
                self.run_sync(arg, "-1")

    def test_non_finite_throttle_rejected(self):
        # argparse takes these as floats and they pass a bare < 0 check, then
        # blow up inside sleep() after the first identity has already synced
        for value in ("nan", "inf"):
            with pytest.raises(CommandError):
                self.run_sync("--throttle", value)

    def test_slice_skips_purge(self):
        self.user2.is_active = False
        self.user2.save()
        before = self.doc_ids(self.identity2.pk)
        assert before
        output = self.run_sync("--limit", "1")
        assert "skipping purge of deactivated identities" in output
        # the purge covers the whole estate, so a sliced run must not start it
        assert self.doc_ids(self.identity2.pk) == before

    def test_throttle_pauses_between_identities(self, monkeypatch):
        pauses = []
        monkeypatch.setattr(journal_command, "sleep", pauses.append)
        self.run_sync("--throttle", "0.01")
        assert pauses == [0.01, 0.01]


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


@pytest.mark.django_db(databases="__all__")
class TestIdxSyncRemote:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.index = JournalIndex.instance()
        self.index.delete_all()
        self.book = Edition.objects.create(title="Hyperion")
        self.owner = _make_remote_identity("remotereader")
        obj = {
            "id": "https://remote.example/review/1",
            "type": "Review",
            "name": "Great Book",
            "content": "review body",
            "mediaType": "text/markdown",
            "published": "2026-01-01T00:00:00+00:00",
        }
        self.post = Post.objects.create(
            author_id=self.owner.pk,
            local=False,
            object_uri=obj["id"],
            content=obj["content"],
            type="Article",
            type_data={"object": {"relatedWith": [obj]}},
            visibility=Post.Visibilities.public,
            state="fanned_out",
        )
        self.review = Review.update_by_ap_object(self.owner, self.book, obj, self.post)
        assert self.review is not None

    def run_sync(self, *args) -> str:
        out = StringIO()
        call_command("journal", "idx-sync", *args, stdout=out)
        return out.getvalue()

    def doc_ids(self, owner_id: int) -> set[str]:
        ids = self.index.get_doc_ids_by_owner(owner_id)
        assert ids is not None
        return ids

    def test_add_missing_remote_docs(self):
        assert self.doc_ids(self.owner.pk) == {str(self.post.pk)}
        self.index.delete_by_owner([self.owner.pk])
        self.run_sync("--remote")
        assert self.doc_ids(self.owner.pk) == {str(self.post.pk)}

    def test_repair_dangling_doc(self):
        # replicate the doc shape indexed before the #1761 fix: keyed by
        # piece with no post_id, counted by item post search but never
        # returned
        self.index.delete_by_owner([self.owner.pk])
        self.index.insert_docs(
            [
                {
                    "id": f"p{self.review.pk}",
                    "piece_id": [self.review.pk],
                    "piece_class": ["Review"],
                    "item_id": [self.book.pk],
                    "item_class": ["Edition"],
                    "content": ["review body"],
                    "created": 1700000000,
                    "owner_id": self.owner.pk,
                    "visibility": 0,
                }
            ]
        )
        self.run_sync("--remote")
        assert self.doc_ids(self.owner.pk) == {str(self.post.pk)}
        q = JournalQueryParser("type:review", 1)
        q.filter_by_viewer(None)
        q.filter("item_id", self.book.pk)
        r = self.index.search(q)
        posts = list(r.posts)
        assert r.total == len(posts) == 1
        assert posts[0].pk == self.post.pk

    def test_purge_deleted_remote_identity(self):
        assert self.doc_ids(self.owner.pk)
        self.owner.deleted = timezone.now()
        self.owner.save()
        output = self.run_sync("--remote")
        assert "1 deactivated identities" in output
        assert self.doc_ids(self.owner.pk) == set()

    def test_local_sync_leaves_remote_docs(self):
        self.run_sync()
        assert self.doc_ids(self.owner.pk) == {str(self.post.pk)}

    def test_sliced_remote_run_skips_full_collection_scan(self, monkeypatch):
        # the full scan runs before the slice would be applied, so paying it
        # per slice would multiply the load slicing exists to spread out
        calls = []
        original = JournalIndex.get_indexed_owner_ids

        def counted(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(JournalIndex, "get_indexed_owner_ids", counted)
        output = self.run_sync("--remote", "--limit", "1")
        assert "skipping the full-collection scan" in output
        assert calls == []
        self.run_sync("--remote")
        assert calls == [1]

    def test_purge_skipped_when_indexed_owners_unavailable(self, monkeypatch):
        before = self.doc_ids(self.owner.pk)
        assert before
        self.owner.deleted = timezone.now()
        self.owner.save()
        monkeypatch.setattr(JournalIndex, "get_indexed_owner_ids", lambda self: None)
        output = self.run_sync("--remote")
        assert "purge of deactivated identities will be skipped" in output
        assert "0 deactivated identities" in output
        # without the indexed-owner list the purge would issue one
        # delete-by-filter per deactivated remote identity, nearly all of them
        # matching nothing; leaving the docs is the cheaper wrong answer
        assert self.doc_ids(self.owner.pk) == before

    def test_remote_dry_run(self):
        self.index.delete_by_owner([self.owner.pk])
        output = self.run_sync("--remote", "--dry-run")
        assert "would be" in output
        assert self.doc_ids(self.owner.pk) == set()

    def _item_review_posts_count(self) -> tuple[int, int]:
        q = JournalQueryParser("type:review", 1)
        q.filter_by_viewer(None)
        q.filter("item_id", self.book.pk)
        q.filter("post_id", ">0")
        r = self.index.search(q)
        return r.total, len(list(r.posts))

    def test_remote_sync_rewrites_doc_of_pruned_post(self):
        post_pk = self.post.pk
        assert self.doc_ids(self.owner.pk) == {str(post_pk)}
        # takahe pruned the post; the doc still claims post_id, so it is
        # counted while its post can never be returned
        self.post.delete()
        assert self._item_review_posts_count() == (1, 0)
        self.run_sync("--remote")
        # same doc id, but content rewritten without the dead post_id
        assert self.doc_ids(self.owner.pk) == {str(post_pk)}
        assert self._item_review_posts_count() == (0, 0)

    def test_remote_sync_cleans_docs_of_pieceless_owner(self):
        assert self.doc_ids(self.owner.pk) == {str(self.post.pk)}
        # queryset delete bypasses Piece.delete()'s index cleanup, leaving
        # a stale doc while the owner no longer has any piece
        Review.objects.filter(pk=self.review.pk).delete()
        self.run_sync("--remote")
        assert self.doc_ids(self.owner.pk) == set()

    def test_remote_lone_comment_refetch_updates_doc(self):
        # a comment without a sibling mark gets its own doc; a pruned then
        # refetched post must be relinked and the doc refreshed even though
        # Comment does not index itself on save
        obj = {
            "id": "https://remote.example/comment/1",
            "type": "Comment",
            "content": "short comment",
            "published": "2026-01-01T00:00:00+00:00",
        }
        post1 = Post.objects.create(
            author_id=self.owner.pk,
            local=False,
            object_uri=obj["id"],
            content=obj["content"],
            type="Note",
            type_data={"object": {"relatedWith": [obj]}},
            visibility=Post.Visibilities.public,
            state="fanned_out",
        )
        comment = Comment.update_by_ap_object(self.owner, self.book, obj, post1)
        assert comment is not None
        self.run_sync("--remote")
        assert str(post1.pk) in self.doc_ids(self.owner.pk)
        post1.delete()
        post2 = Post.objects.create(
            author_id=self.owner.pk,
            local=False,
            object_uri=obj["id"],
            content=obj["content"],
            type="Note",
            type_data={"object": {"relatedWith": [obj]}},
            visibility=Post.Visibilities.public,
            state="fanned_out",
        )
        Comment.update_by_ap_object(self.owner, self.book, obj, post2)
        ids = self.doc_ids(self.owner.pk)
        assert str(post2.pk) in ids
        assert str(post1.pk) not in ids
