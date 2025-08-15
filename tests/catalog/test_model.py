import pytest

from catalog.book.models import Edition
from catalog.common.jsondata import decrypt_str, encrypt_str
from catalog.common.models import Item


@pytest.mark.django_db(databases="__all__")
class TestCatalog:
    def test_merge(self):
        hyperion_hardcover = Edition.objects.create(title="Hyperion")
        hyperion_hardcover.pages = 481
        hyperion_hardcover.isbn = "9780385249492"
        hyperion_hardcover.save()
        hyperion_print = Edition.objects.create(title="Hyperion")
        hyperion_print.pages = 500
        hyperion_print.isbn = "9780553283686"
        hyperion_print.save()

        hyperion_hardcover.merge_to(hyperion_print)
        assert hyperion_hardcover.merged_to_item == hyperion_print

    def test_merge_resolve(self):
        hyperion_hardcover = Edition.objects.create(title="Hyperion")
        hyperion_hardcover.pages = 481
        hyperion_hardcover.isbn = "9780385249492"
        hyperion_hardcover.save()
        hyperion_print = Edition.objects.create(title="Hyperion")
        hyperion_print.pages = 500
        hyperion_print.isbn = "9780553283686"
        hyperion_print.save()
        hyperion_ebook = Edition(title="Hyperion")
        hyperion_ebook.asin = "B0043M6780"
        hyperion_ebook.save()

        hyperion_hardcover.merge_to(hyperion_print)
        hyperion_print.merge_to(hyperion_ebook)
        resolved = Item.get_by_url(hyperion_hardcover.url, True)
        assert resolved == hyperion_ebook

    def test_encypted_field(self):
        o = "Hello, World!"
        e = encrypt_str(o)
        d = decrypt_str(e)
        assert o == d
