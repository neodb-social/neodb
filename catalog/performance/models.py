from catalog.common import *
from django.utils.translation import gettext_lazy as _
from django.db import models


class Performance(Item):
    category = ItemCategory.Performance
    url_path = "performance"
    demonstrative = _("这个演出")
    douban_drama = LookupIdDescriptor(IdType.DoubanDrama)
    other_title = jsondata.ArrayField(
        verbose_name=_("其它标题"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=False,
        blank=False,
        default=list,
    )
    genre = jsondata.ArrayField(
        verbose_name=_("类型"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )
    version = jsondata.ArrayField(
        verbose_name=_("版本"),
        base_field=models.CharField(),
        null=False,
        blank=False,
        default=list,
    )
    director = jsondata.ArrayField(
        verbose_name=_("导演"),
        base_field=models.CharField(),
        null=False,
        blank=False,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("编剧"),
        base_field=models.CharField(),
        null=False,
        blank=False,
        default=list,
    )
    actor = jsondata.ArrayField(
        verbose_name=_("主演"),
        base_field=models.CharField(),
        null=False,
        blank=False,
        default=list,
    )
    composer = jsondata.ArrayField(
        verbose_name=_("作曲"),
        base_field=models.CharField(),
        null=False,
        blank=False,
        default=list,
    )
    choreographer = jsondata.ArrayField(
        verbose_name=_("编舞"),
        base_field=models.CharField(),
        null=False,
        blank=False,
        default=list,
    )
    troupe = jsondata.ArrayField(
        verbose_name=_("剧团"),
        base_field=models.CharField(),
        null=False,
        blank=False,
        default=list,
    )
    theatre = jsondata.ArrayField(
        verbose_name=_("剧场"),
        base_field=models.CharField(),
        null=False,
        blank=False,
        default=list,
    )
    opening_date = jsondata.CharField(
        verbose_name=_("演出日期"), max_length=100, null=True, blank=True
    )
    official_site = jsondata.CharField(
        verbose_name=_("官方网站"), max_length=1000, null=True, blank=True
    )
    METADATA_COPY_LIST = [
        "title",
        "brief",
        "other_title",
        "genre",
        "version",
        "director",
        "playwright",
        "actor",
        "composer",
        "choreographer",
        "troupe",
        "theatre",
        "opening_date",
        "official_site",
    ]

    class Meta:
        proxy = True
