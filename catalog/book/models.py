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
from catalog.common.models import *
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
    pages: str | None = None
    series: str | None = None
    imprint: str | None = None


class EditionSchema(EditionInSchema, BaseSchema):
    pass


class Edition(Item):
    category = ItemCategory.Book
    url_path = "book"
    demonstrative = _("这本书")

    isbn = PrimaryLookupIdDescriptor(IdType.ISBN)
    asin = PrimaryLookupIdDescriptor(IdType.ASIN)
    cubn = PrimaryLookupIdDescriptor(IdType.CUBN)
    # douban_book = LookupIdDescriptor(IdType.DoubanBook)
    # goodreads = LookupIdDescriptor(IdType.Goodreads)

    METADATA_COPY_LIST = [
        "title",
        "subtitle",
        "orig_title",
        "language",
        "author",
        "translator",
        "pub_house",
        "pub_year",
        "pub_month",
        "imprint",
        "binding",
        "pages",
        "series",
        "price",
        "brief",
        "contents",
    ]
    subtitle = jsondata.CharField(
        _("副标题"), null=True, blank=True, default=None, max_length=500
    )
    orig_title = jsondata.CharField(
        _("原名"), null=True, blank=True, default=None, max_length=500
    )
    author = jsondata.ArrayField(
        verbose_name=_("作者"),
        base_field=models.CharField(max_length=500),
        null=False,
        blank=False,
        default=list,
    )
    translator = jsondata.ArrayField(
        verbose_name=_("译者"),
        base_field=models.CharField(max_length=500),
        null=True,
        blank=True,
        default=list,
    )
    language = jsondata.CharField(
        _("语言"), null=True, blank=True, default=None, max_length=500
    )
    pub_house = jsondata.CharField(
        _("出版社"), null=True, blank=False, default=None, max_length=500
    )
    pub_year = jsondata.IntegerField(
        _("出版年份"),
        null=True,
        blank=False,
        validators=[MinValueValidator(1), MaxValueValidator(2999)],
    )
    pub_month = jsondata.IntegerField(
        _("出版月份"),
        null=True,
        blank=False,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    binding = jsondata.CharField(
        _("装订"), null=True, blank=True, default=None, max_length=500
    )
    pages = jsondata.IntegerField(_("页数"), blank=True, default=None)
    series = jsondata.CharField(
        _("丛书"), null=True, blank=True, default=None, max_length=500
    )
    contents = jsondata.TextField(_("目录"), null=True, blank=True, default=None)
    price = jsondata.CharField(_("价格"), null=True, blank=True, max_length=500)
    imprint = jsondata.CharField(_("出品方"), null=True, blank=True, max_length=500)

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
            if w["model"] == "Work":
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
            .exclude(pk=self.pk)
            .exclude(is_deleted=True)
            .exclude(merged_to_item__isnull=False)
        )


class Work(Item):
    category = ItemCategory.Book
    url_path = "book/work"
    douban_work = PrimaryLookupIdDescriptor(IdType.DoubanBook_Work)
    goodreads_work = PrimaryLookupIdDescriptor(IdType.Goodreads_Work)
    editions = models.ManyToManyField(Edition, related_name="works")


class Series(Item):
    category = ItemCategory.Book
    url_path = "book/series"
    # douban_serie = LookupIdDescriptor(IdType.DoubanBook_Serie)
    # goodreads_serie = LookupIdDescriptor(IdType.Goodreads_Serie)

    class Meta:
        proxy = True
