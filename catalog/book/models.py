"""
Models for Book

Series -> Work -> Edition

Series is not fully implemented at the moment

Goodreads
Famous works have many editions

Google Books:
only has Edition level ("volume") data

Douban:
old editions has only CUBN(Chinese Unified Book Number)
work data seems asymmetric (a book links to a work, but may not listed in that work as one of its editions)

"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from catalog.common import (
    BaseSchema,
    ExternalResource,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    ItemSchema,
    ItemType,
    PrimaryLookupIdDescriptor,
    jsondata,
)

from .utils import *


class EditionInSchema(ItemInSchema):
    subtitle: str | None = None
    orig_title: str | None = None
    author: list[str]
    translator: list[str]
    language: str | None = None
    pub_house: str | None = None
    pub_year: int | None = None
    pub_month: int | None = None
    binding: str | None = None
    price: str | None = None
    pages: int | str | None = None
    series: str | None = None
    imprint: str | None = None


class EditionSchema(EditionInSchema, BaseSchema):
    isbn: str | None = None
    pass


class Edition(Item):
    category = ItemCategory.Book
    url_path = "book"

    isbn = PrimaryLookupIdDescriptor(IdType.ISBN)
    asin = PrimaryLookupIdDescriptor(IdType.ASIN)
    cubn = PrimaryLookupIdDescriptor(IdType.CUBN)
    # douban_book = LookupIdDescriptor(IdType.DoubanBook)
    # goodreads = LookupIdDescriptor(IdType.Goodreads)

    METADATA_COPY_LIST = [
        "title",
        "subtitle",
        "author",
        "pub_house",
        "pub_year",
        "pub_month",
        "language",
        "orig_title",
        "translator",
        "series",
        "imprint",
        "binding",
        "pages",
        "price",
        "brief",
        "contents",
    ]
    subtitle = jsondata.CharField(
        _("subtitle"), null=True, blank=True, default=None, max_length=500
    )
    orig_title = jsondata.CharField(
        _("original title"), null=True, blank=True, default=None, max_length=500
    )
    author = jsondata.ArrayField(
        verbose_name=_("author"),
        base_field=models.CharField(max_length=500),
        null=False,
        blank=False,
        default=list,
    )
    translator = jsondata.ArrayField(
        verbose_name=_("translator"),
        base_field=models.CharField(max_length=500),
        null=True,
        blank=True,
        default=list,
    )
    language = jsondata.CharField(
        _("language"), null=True, blank=True, default=None, max_length=500
    )
    pub_house = jsondata.CharField(
        _("publisher"), null=True, blank=False, default=None, max_length=500
    )
    pub_year = jsondata.IntegerField(
        _("publication year"),
        null=True,
        blank=False,
        validators=[MinValueValidator(1), MaxValueValidator(2999)],
    )
    pub_month = jsondata.IntegerField(
        _("publication month"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    binding = jsondata.CharField(
        _("binding"), null=True, blank=True, default=None, max_length=500
    )
    pages = jsondata.IntegerField(_("pages"), blank=True, default=None)
    series = jsondata.CharField(
        _("series"), null=True, blank=True, default=None, max_length=500
    )
    contents = jsondata.TextField(_("contents"), null=True, blank=True, default=None)
    price = jsondata.CharField(_("price"), null=True, blank=True, max_length=500)
    imprint = jsondata.CharField(_("imprint"), null=True, blank=True, max_length=500)

    @property
    def isbn10(self):
        return isbn_13_to_10(self.isbn)

    @isbn10.setter
    def isbn10(self, value):
        self.isbn = isbn_10_to_13(value)

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.ISBN,
            IdType.ASIN,
            IdType.CUBN,
            IdType.DoubanBook,
            IdType.Goodreads,
            IdType.GoogleBooks,
        ]
        return [(i.value, i.label) for i in id_types]

    @classmethod
    def lookup_id_cleanup(cls, lookup_id_type, lookup_id_value):
        if lookup_id_type in [IdType.ASIN.value, IdType.ISBN.value]:
            return detect_isbn_asin(lookup_id_value)
        return super().lookup_id_cleanup(lookup_id_type, lookup_id_value)

    def update_linked_items_from_external_resource(self, resource):
        """add Work from resource.metadata['work'] if not yet"""
        links = resource.required_resources + resource.related_resources
        for w in links:
            if w.get("model") == "Work":
                work = Work.objects.filter(
                    primary_lookup_id_type=w["id_type"],
                    primary_lookup_id_value=w["id_value"],
                ).first()
                if work and work not in self.works.all():
                    self.works.add(work)
                # if not work:
                #     _logger.info(f'Unable to find link for {w["url"]}')

    def get_related_books(self):
        works = list(self.works.all())
        return (
            Edition.objects.filter(works__in=works)
            .distinct()
            .exclude(pk=self.pk)
            .exclude(is_deleted=True)
            .exclude(merged_to_item__isnull=False)
            .order_by("title")
        )


class Work(Item):
    category = ItemCategory.Book
    url_path = "book/work"
    douban_work = PrimaryLookupIdDescriptor(IdType.DoubanBook_Work)
    goodreads_work = PrimaryLookupIdDescriptor(IdType.Goodreads_Work)
    editions = models.ManyToManyField(Edition, related_name="works")
    # TODO: we have many duplicates due to 302
    # a lazy fix is to remove smaller DoubanBook_Work ids
    # but ideally deal with 302 in scrape().


class Series(Item):
    category = ItemCategory.Book
    url_path = "book/series"
    # douban_serie = LookupIdDescriptor(IdType.DoubanBook_Serie)
    # goodreads_serie = LookupIdDescriptor(IdType.Goodreads_Serie)

    class Meta:
        proxy = True
