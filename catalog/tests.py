from django.test import TestCase

from catalog.book.tests import *
from catalog.game.tests import *
from catalog.movie.tests import *
from catalog.music.tests import *
from catalog.performance.tests import *
from catalog.podcast.tests import *
from catalog.tv.tests import *


# imported tests with same name might be ignored silently
class CatalogCase(TestCase):
    databases = "__all__"

    def setUp(self):
        self.hyperion_hardcover = Edition.objects.create(title="Hyperion")
        self.hyperion_hardcover.pages = 481
        self.hyperion_hardcover.isbn = "9780385249492"
        self.hyperion_hardcover.save()
        self.hyperion_print = Edition.objects.create(title="Hyperion")
        self.hyperion_print.pages = 500
        self.hyperion_print.isbn = "9780553283686"
        self.hyperion_print.save()
        self.hyperion_ebook = Edition(title="Hyperion")
        self.hyperion_ebook.asin = "B0043M6780"
        self.hyperion_ebook.save()
        self.andymion_print = Edition.objects.create(title="Andymion", pages=42)
        # serie = Serie(title="Hyperion Cantos")
        self.hyperion = Work(title="Hyperion")
        self.hyperion.save()

    def test_merge(self):
        self.hyperion_hardcover.merge_to(self.hyperion_print)
        self.assertEqual(self.hyperion_hardcover.merged_to_item, self.hyperion_print)

    def test_merge_resolve(self):
        self.hyperion_hardcover.merge_to(self.hyperion_print)
        self.hyperion_print.merge_to(self.hyperion_ebook)
        resloved = Item.get_by_url(self.hyperion_hardcover.url, True)
        self.assertEqual(resloved, self.hyperion_ebook)
