from django.test import TestCase

from catalog.models import Edition
from users.models import User

from ..models import *


class SearchTest(TestCase):
    databases = "__all__"

    def setUp(self):
        self.book1 = Edition.objects.create(title="Hyperion")
        self.book2 = Edition.objects.create(title="Andymion")
        self.user1 = User.register(email="x@y.com", username="userx")
        self.index = JournalIndex.instance()
        self.index.delete_by_owner([self.user1.identity.pk])

    def test_post(self):
        mark = Mark(self.user1.identity, self.book1)
        mark.update(ShelfType.WISHLIST, "a gentle comment", 9, ["Sci-Fi", "fic"], 0)
        mark = Mark(self.user1.identity, self.book2)
        mark.update(ShelfType.WISHLIST, "a gentle comment", None, ["nonfic"], 1)
        q = JournalQueryParser("gentle")
        q.filter_by_owner(self.user1.identity)
        r = self.index.search(q)
        self.assertEqual(r.total, 2)
