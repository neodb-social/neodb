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

from functools import cached_property
from typing import TYPE_CHECKING

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger
from ninja import Field

from catalog.common import (
    BaseSchema,
    ExternalResource,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    PrimaryLookupIdDescriptor,
    jsondata,
)
from catalog.common.models import (
    LIST_OF_ONE_PLUS_STR_SCHEMA,
    LOCALE_CHOICES_JSONFORM,
    LanguageListField,
)
from common.models import uniq

from .utils import *


class EditionInSchema(ItemInSchema):
    subtitle: str | None = Field(default=None, alias="display_subtitle")
    orig_title: str | None = None
    author: list[str]
    translator: list[str]
    language: list[str]
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


EDITION_LOCALIZED_TITLE_SCHEMA = {
    "type": "list",
    "items": {
        "type": "dict",
        "keys": {
            "lang": {
                "type": "string",
                "title": _("locale"),
                "choices": LOCALE_CHOICES_JSONFORM,
            },
            "text": {"type": "string", "title": _("text content")},
        },
        "required": ["lang", "text"],
    },
    "minItems": 1,
    "maxItems": 1,
    # "uniqueItems": True,
}

EDITION_LOCALIZED_SUBTITLE_SCHEMA = {
    "type": "list",
    "items": {
        "type": "dict",
        "keys": {
            "lang": {
                "type": "string",
                "title": _("locale"),
                "choices": LOCALE_CHOICES_JSONFORM,
            },
            "text": {"type": "string", "title": _("text content")},
        },
        "required": ["lang", "text"],
    },
    "minItems": 0,
    "maxItems": 1,
    # "uniqueItems": True,
}


class Edition(Item):
    if TYPE_CHECKING:
        works: "models.ManyToManyField[Work, Edition]"

    class BookFormat(models.TextChoices):
        PAPERBACK = "paperback", _("Paperback")
        HARDCOVER = "hardcover", _("Hardcover")
        EBOOK = "ebook", _("eBook")
        AUDIOBOOK = "audiobook", _("Audiobook")
        # GRAPHICNOVEL = "graphicnovel", _("GraphicNovel")
        WEB = "web", _("Web Fiction")
        OTHER = "other", _("Other")

    schema = EditionSchema
    category = ItemCategory.Book
    url_path = "book"

    isbn = PrimaryLookupIdDescriptor(IdType.ISBN)
    asin = PrimaryLookupIdDescriptor(IdType.ASIN)
    cubn = PrimaryLookupIdDescriptor(IdType.CUBN)
    # douban_book = LookupIdDescriptor(IdType.DoubanBook)
    # goodreads = LookupIdDescriptor(IdType.Goodreads)

    METADATA_COPY_LIST = [
        "localized_title",
        "localized_subtitle",
        # "title",
        # "subtitle",
        "author",
        "format",
        "pub_house",
        "pub_year",
        "pub_month",
        "language",
        "orig_title",
        "other_title",
        "translator",
        "series",
        "imprint",
        "binding",
        "pages",
        "price",
        # "brief",
        "localized_description",
        "contents",
    ]
    # force Edition to have only one title
    localized_title_schema = EDITION_LOCALIZED_TITLE_SCHEMA
    localized_subtitle = jsondata.JSONField(
        verbose_name=_("subtitle"),
        null=False,
        blank=True,
        default=list,
        schema=EDITION_LOCALIZED_SUBTITLE_SCHEMA,
    )
    # subtitle = jsondata.CharField(
    #     _("subtitle"), null=True, blank=True, default=None, max_length=500
    # )
    orig_title = jsondata.CharField(
        _("original title"), null=True, blank=True, max_length=500
    )
    other_title = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("other title"),
        null=True,
        blank=True,
        default=list,
    )
    author = jsondata.JSONField(
        verbose_name=_("author"),
        null=False,
        blank=False,
        default=list,
        schema=LIST_OF_ONE_PLUS_STR_SCHEMA,
    )
    translator = jsondata.ArrayField(
        verbose_name=_("translator"),
        base_field=models.CharField(max_length=500),
        null=True,
        blank=True,
        default=list,
    )
    format = jsondata.CharField(
        _("book format"),
        blank=True,
        max_length=100,
        choices=BookFormat.choices,
    )
    language = LanguageListField()
    pub_house = jsondata.CharField(
        _("publishing house"), null=True, blank=True, max_length=500
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
    binding = jsondata.CharField(_("binding"), null=True, blank=True, max_length=500)
    pages = jsondata.IntegerField(_("pages"), blank=True, default=None)
    series = jsondata.CharField(_("series"), null=True, blank=True, max_length=500)
    contents = jsondata.TextField(_("contents"), null=True, blank=True)
    price = jsondata.CharField(_("price"), null=True, blank=True, max_length=500)
    imprint = jsondata.CharField(_("imprint"), null=True, blank=True, max_length=500)

    def get_localized_subtitle(self) -> str | None:
        return self.localized_subtitle[0]["text"] if self.localized_subtitle else None

    @property
    def display_subtitle(self) -> str | None:
        return self.get_localized_subtitle()

    def to_indexable_titles(self) -> list[str]:
        titles = [t["text"] for t in self.localized_title if t["text"]]
        titles += [t["text"] for t in self.localized_subtitle if t["text"]]
        titles += [self.orig_title] if self.orig_title else []
        titles += [t for t in self.other_title if t]  # type: ignore
        return list(set(titles))

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
            IdType.Qidian,
            IdType.JJWXC,
        ]
        return [(i.value, i.label) for i in id_types]

    @classmethod
    def lookup_id_cleanup(cls, lookup_id_type: str | IdType, lookup_id_value: str):
        if lookup_id_type in [IdType.ASIN.value, IdType.ISBN.value]:
            return detect_isbn_asin(lookup_id_value)
        return super().lookup_id_cleanup(lookup_id_type, lookup_id_value)

    def merge_to(self, to_item: "Edition | None"):  # type: ignore[reportIncompatibleMethodOverride]
        super().merge_to(to_item)
        if to_item:
            if self.merge_title():
                self.save()
            for work in self.works.all():
                to_item.works.add(work)
        self.works.clear()

    def delete(self, using=None, keep_parents=False, soft=True, *args, **kwargs):
        if soft:
            self.works.clear()
        return super().delete(using, keep_parents, soft, *args, **kwargs)

    def update_linked_items_from_external_resource(self, resource):
        """add Work from resource.metadata['work'] if not yet"""
        links = resource.required_resources + resource.related_resources
        for w in links:
            if w.get("model") == "Work":
                work_res = ExternalResource.objects.filter(
                    id_type=w["id_type"], id_value=w["id_value"]
                ).first()
                if work_res:
                    work = work_res.item
                    if not work:
                        logger.warning(f"Unable to find work for {work_res}")
                else:
                    logger.warning(
                        f"Unable to find resource for {w['id_type']}:{w['id_value']}"
                    )
                    work = Work.objects.filter(
                        primary_lookup_id_type=w["id_type"],
                        primary_lookup_id_value=w["id_value"],
                    ).first()
                if work and work not in self.works.all():
                    self.works.add(work)

    def merge_data_from_external_resource(
        self, p: "ExternalResource", ignore_existing_content: bool = False
    ):
        super().merge_data_from_external_resource(p, ignore_existing_content)
        self.merge_title()

    def merge_title(self) -> bool:
        # Edition should have only one title, so extra titles will be merged to other_title, return True if updated
        if len(self.localized_title) <= 1:
            return False
        titles = self.localized_title
        self.localized_title = []
        for t in titles:
            if isinstance(t, dict) and t.get("text"):
                if len(self.localized_title) == 0:
                    self.localized_title = [t]
                elif t["text"] not in self.other_title:
                    self.other_title += [t["text"]]  # type: ignore
        return True

    @property
    def sibling_items(self):
        works = list(self.works.all())
        return (
            Edition.objects.filter(works__in=works)
            .exclude(pk=self.pk)
            .exclude(is_deleted=True)
            .exclude(merged_to_item__isnull=False)
            .order_by("-metadata__pub_year")
        )

    @cached_property
    def additional_title(self) -> list[str]:
        title = self.display_title
        return [
            t["text"] for t in self.localized_title if t["text"] != title
        ] + self.other_title  # type: ignore

    @property
    def title_deco(self):
        a = [str(i) for i in [self.pub_house, self.pub_year] if i]
        return f"({' '.join(a)})" if a else ""

    def has_related_books(self):
        works = list(self.works.all())
        if not works:
            return False
        return Edition.objects.filter(works__in=works).exclude(pk=self.pk).exists()

    def link_to_related_book(self, target: "Edition") -> bool:
        if target == self or target.is_deleted or target.merged_to_item:
            return False
        if target.works.all().exists():
            for work in target.works.all():
                self.works.add(work)
                work.localized_title = uniq(work.localized_title + self.localized_title)
                work.save()
        elif self.works.all().exists():
            for work in self.works.all():
                target.works.add(work)
                work.localized_title = uniq(
                    work.localized_title + target.localized_title
                )
                work.save()
        else:
            work = Work.objects.create(localized_title=self.localized_title)
            work.editions.add(self, target)
            # work.localized_title = self.localized_title
            # work.save()
        return True

    def unlink_from_all_works(self):
        self.works.clear()

    def has_works(self):
        return self.works.all().exists()


class Work(Item):
    category = ItemCategory.Book
    url_path = "book/work"
    douban_work = PrimaryLookupIdDescriptor(IdType.DoubanBook_Work)
    goodreads_work = PrimaryLookupIdDescriptor(IdType.Goodreads_Work)
    editions = models.ManyToManyField(Edition, related_name="works")
    language = LanguageListField()
    author = jsondata.ArrayField(
        verbose_name=_("author"),
        base_field=models.CharField(max_length=500),
        null=True,
        blank=True,
        default=list,
    )
    # other_title = jsondata.ArrayField(
    #     verbose_name=_("other title"),
    #     base_field=models.CharField(blank=True, default="", max_length=200),
    #     null=True,
    #     blank=True,
    #     default=list,
    # )
    METADATA_COPY_LIST = [
        "localized_title",
        "author",
        "language",
        "localized_description",
    ]
    # TODO: we have many duplicates due to 302
    # a lazy fix is to remove smaller DoubanBook_Work ids
    # but ideally deal with 302 in scrape().

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.WikiData,
            IdType.DoubanBook_Work,
            IdType.Goodreads_Work,
        ]
        return [(i.value, i.label) for i in id_types]

    def merge_to(self, to_item: "Work | None"):  # type: ignore[reportIncompatibleMethodOverride]
        super().merge_to(to_item)
        if not to_item:
            return
        for edition in self.editions.all():
            to_item.editions.add(edition)
        self.editions.clear()
        to_item.language = uniq(to_item.language + self.language)  # type: ignore
        to_item.localized_title = uniq(to_item.localized_title + self.localized_title)
        to_item.save()

    def delete(self, using=None, keep_parents=False, soft=True, *args, **kwargs):
        if soft:
            self.editions.clear()
        return super().delete(using, keep_parents, soft, *args, **kwargs)

    @property
    def cover_image_url(self):
        url = super().cover_image_url
        if url:
            return url
        e = next(filter(lambda e: e.cover_image_url, self.editions.all()), None)
        return e.cover_image_url if e else None

    def update_linked_items_from_external_resource(self, resource):
        """add Edition from resource.metadata['required_resources'] if not yet"""
        links = resource.required_resources + resource.related_resources
        for e in links:
            if e.get("model") == "Edition":
                edition_res = ExternalResource.objects.filter(
                    id_type=e["id_type"], id_value=e["id_value"]
                ).first()
                if edition_res:
                    edition = edition_res.item
                    if not edition:
                        logger.warning(f"Unable to find edition for {edition_res}")
                else:
                    logger.warning(
                        f"Unable to find resource for {e['id_type']}:{e['id_value']}"
                    )
                    edition = Edition.objects.filter(
                        primary_lookup_id_type=e["id_type"],
                        primary_lookup_id_value=e["id_value"],
                    ).first()
                if edition and edition not in self.editions.all():
                    self.editions.add(edition)


class Series(Item):
    category = ItemCategory.Book
    url_path = "book/series"
    # douban_serie = LookupIdDescriptor(IdType.DoubanBook_Serie)
    # goodreads_serie = LookupIdDescriptor(IdType.Goodreads_Serie)

    class Meta:
        proxy = True
