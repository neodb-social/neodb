from io import StringIO

import pytest
from django.core.management import call_command

from catalog.models import Edition
from journal.models import Mark, Review, ShelfType
from journal.search import JournalIndex
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
