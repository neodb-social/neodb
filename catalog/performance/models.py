from catalog.common import *
from django.utils.translation import gettext_lazy as _
from django.db import models
from catalog.common.utils import DEFAULT_ITEM_COVER
from functools import cached_property


class Performance(Item):
    type = ItemType.Performance
    category = ItemCategory.Performance
    url_path = "performance"
    demonstrative = _("这部剧作")
    orig_title = jsondata.CharField(
        verbose_name=_("原名"), blank=True, default="", max_length=500
    )
    other_title = jsondata.ArrayField(
        verbose_name=_("其它标题"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    genre = jsondata.ArrayField(
        verbose_name=_("类型"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=False,
        blank=False,
        default=list,
    )
    language = jsondata.ArrayField(
        verbose_name=_("语言"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    director = jsondata.ArrayField(
        verbose_name=_("导演"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("编剧"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    orig_creator = jsondata.ArrayField(
        verbose_name=_("原作者"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    composer = jsondata.ArrayField(
        verbose_name=_("作曲"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    choreographer = jsondata.ArrayField(
        verbose_name=_("编舞"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    performer = jsondata.ArrayField(
        verbose_name=_("演员"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    troupe = jsondata.ArrayField(
        verbose_name=_("剧团"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    crew = jsondata.ArrayField(
        verbose_name=_("其他演职人员和团体"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    location = jsondata.ArrayField(
        verbose_name=_("剧场空间"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    opening_date = jsondata.CharField(
        verbose_name=_("首演日期"), max_length=100, null=True, blank=True
    )
    closing_date = jsondata.CharField(
        verbose_name=_("结束日期"), max_length=100, null=True, blank=True
    )
    official_site = jsondata.CharField(
        verbose_name=_("官方网站"), max_length=1000, null=True, blank=True
    )
    METADATA_COPY_LIST = [
        "title",
        "brief",
        "orig_title",
        "other_title",
        "genre",
        "language",
        "opening_date",
        "closing_date",
        "troupe",
        "location",
        "director",
        "playwright",
        "orig_creator",
        "composer",
        "choreographer",
        "performer",
        "crew",
        "official_site",
    ]

    @cached_property
    def all_productions(self):
        return (
            self.productions.all()
            .order_by("metadata__opening_date", "title")
            .filter(is_deleted=False, merged_to_item=None)
        )


class PerformanceProduction(Item):
    type = ItemType.PerformanceProduction
    category = ItemCategory.Performance
    url_path = "performance/production"
    demonstrative = _("这次上演")
    show = models.ForeignKey(
        Performance, null=True, on_delete=models.SET_NULL, related_name="productions"
    )
    orig_title = jsondata.CharField(
        verbose_name=_("原名"), blank=True, default="", max_length=500
    )
    other_title = jsondata.ArrayField(
        verbose_name=_("其它标题"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    language = jsondata.ArrayField(
        verbose_name=_("语言"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    director = jsondata.ArrayField(
        verbose_name=_("导演"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("编剧"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    orig_creator = jsondata.ArrayField(
        verbose_name=_("原作者"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    composer = jsondata.ArrayField(
        verbose_name=_("作曲"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    choreographer = jsondata.ArrayField(
        verbose_name=_("编舞"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    performer = jsondata.ArrayField(
        verbose_name=_("演员"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    troupe = jsondata.ArrayField(
        verbose_name=_("剧团"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    crew = jsondata.ArrayField(
        verbose_name=_("其他演职人员和团体"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    location = jsondata.ArrayField(
        verbose_name=_("剧场空间"),
        base_field=models.CharField(blank=True, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    opening_date = jsondata.CharField(
        verbose_name=_("首演日期"), max_length=100, null=True, blank=False
    )
    closing_date = jsondata.CharField(
        verbose_name=_("结束日期"), max_length=100, null=True, blank=True
    )
    official_site = jsondata.CharField(
        verbose_name=_("官方网站"), max_length=1000, null=True, blank=True
    )
    METADATA_COPY_LIST = [
        "title",
        "brief",
        "orig_title",
        "other_title",
        "language",
        "opening_date",
        "closing_date",
        "troupe",
        "location",
        "director",
        "playwright",
        "orig_creator",
        "composer",
        "choreographer",
        "performer",
        "crew",
        "official_site",
    ]

    @property
    def parent_item(self):
        return self.show

    @property
    def display_title(self):
        return f"{self.show.title} {self.title}"

    @property
    def cover_image_url(self):
        return (
            self.cover.url
            if self.cover and self.cover != DEFAULT_ITEM_COVER
            else self.show.cover_image_url
        )

    def update_linked_items_from_external_resource(self, resource):
        for r in resource.required_resources:
            if r["model"] == "Performance":
                resource = ExternalResource.objects.filter(
                    id_type=r["id_type"], id_value=r["id_value"]
                ).first()
                if resource and resource.item:
                    self.show = resource.item
