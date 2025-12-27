import pytest

from catalog.models import Edition
from journal.models import Mark, ShelfType
from journal.search import JournalIndex, JournalQueryParser
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
