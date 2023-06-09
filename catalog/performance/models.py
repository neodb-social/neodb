from functools import cached_property
from django.utils.translation import gettext_lazy as _
from django.db import models
from catalog.common import *
from catalog.common.models import ItemSchema
from catalog.common.utils import DEFAULT_ITEM_COVER
from ninja import Schema


class CrewMemberSchema(Schema):
    name: str
    role: str | None


class PerformanceSchema(ItemSchema):
    orig_title: str | None = None
    other_title: list[str]
    genre: list[str]
    language: list[str]
    opening_date: str | None = None
    closing_date: str | None = None
    director: list[str]
    playwright: list[str]
    orig_creator: list[str]
    composer: list[str]
    choreographer: list[str]
    performer: list[str]
    actor: list[CrewMemberSchema]
    crew: list[CrewMemberSchema]
    official_site: str | None = None


class PerformanceProductionSchema(ItemSchema):
    orig_title: str | None = None
    other_title: list[str]
    language: list[str]
    opening_date: str | None = None
    closing_date: str | None = None
    director: list[str]
    playwright: list[str]
    orig_creator: list[str]
    composer: list[str]
    choreographer: list[str]
    performer: list[str]
    actor: list[CrewMemberSchema]
    crew: list[CrewMemberSchema]
    official_site: str | None = None


_CREW_SCHEMA = {
    "type": "list",
    "items": {
        "type": "dict",
        "keys": {
            "name": {"type": "string", "title": _("名字")},
            "role": {"type": "string", "title": _("职能")},
        },
        "required": ["role", "name"],
    },
    "uniqueItems": True,
}

_ACTOR_SCHEMA = {
    "type": "list",
    "items": {
        "type": "dict",
        "keys": {
            "name": {"type": "string", "title": _("名字"), "placeholder": _("演员名字，必填")},
            "role": {"type": "string", "title": _("角色"), "placeholder": _("也可不填写")},
        },
        "required": ["name"],
    },
    "uniqueItems": True,
}


def _crew_by_role(crew):
    roles = set([c["role"] for c in crew if c.get("role")])
    r = {key: [] for key in roles}
    for c in crew:
        r[c["role"]].append(c["name"])
    return r


class Performance(Item):
    type = ItemType.Performance
    child_class = "PerformanceProduction"
    category = ItemCategory.Performance
    url_path = "performance"
    demonstrative = _("这部剧作")
    orig_title = jsondata.CharField(
        verbose_name=_("原名"), blank=True, default="", max_length=500
    )
    other_title = jsondata.ArrayField(
        verbose_name=_("其它标题"),
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    genre = jsondata.ArrayField(
        verbose_name=_("类型"),
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=False,
        default=list,
    )
    language = jsondata.ArrayField(
        verbose_name=_("语言"),
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    director = jsondata.ArrayField(
        verbose_name=_("导演"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("编剧"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    orig_creator = jsondata.ArrayField(
        verbose_name=_("原作者"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    composer = jsondata.ArrayField(
        verbose_name=_("作曲"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    choreographer = jsondata.ArrayField(
        verbose_name=_("编舞"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    actor = jsondata.JSONField(
        verbose_name=_("演员"),
        null=False,
        blank=True,
        default=list,
        schema=_ACTOR_SCHEMA,
    )
    performer = jsondata.ArrayField(
        verbose_name=_("表演者"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    troupe = jsondata.ArrayField(
        verbose_name=_("剧团"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    crew = jsondata.JSONField(
        verbose_name=_("其他演职人员和团体"),
        null=False,
        blank=True,
        default=list,
        schema=_CREW_SCHEMA,
    )
    location = jsondata.ArrayField(
        verbose_name=_("剧场空间"),
        base_field=models.CharField(blank=False, default="", max_length=500),
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
        "actor",
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

    @cached_property
    def crew_by_role(self):
        return _crew_by_role(self.crew)

    @property
    def child_items(self):
        return self.all_productions


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
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    language = jsondata.ArrayField(
        verbose_name=_("语言"),
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    director = jsondata.ArrayField(
        verbose_name=_("导演"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("编剧"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    orig_creator = jsondata.ArrayField(
        verbose_name=_("原作者"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    composer = jsondata.ArrayField(
        verbose_name=_("作曲"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    choreographer = jsondata.ArrayField(
        verbose_name=_("编舞"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    actor = jsondata.JSONField(
        verbose_name=_("演员"),
        null=False,
        blank=True,
        default=list,
        schema=_ACTOR_SCHEMA,
    )
    performer = jsondata.ArrayField(
        verbose_name=_("表演者"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    troupe = jsondata.ArrayField(
        verbose_name=_("剧团"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    crew = jsondata.JSONField(
        verbose_name=_("其他演职人员和团体"),
        null=False,
        blank=True,
        default=list,
        schema=_CREW_SCHEMA,
    )
    location = jsondata.ArrayField(
        verbose_name=_("剧场空间"),
        base_field=models.CharField(blank=False, default="", max_length=500),
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
        "actor",
        "performer",
        "crew",
        "official_site",
    ]

    @property
    def parent_item(self):
        return self.show

    def set_parent_item(self, value):
        self.show = value

    @property
    def display_title(self):
        return f"{self.show.title if self.show else '♢'} {self.title}"

    @property
    def cover_image_url(self):
        return (
            self.cover.url
            if self.cover and self.cover != DEFAULT_ITEM_COVER
            else self.show.cover_image_url
            if self.show
            else None
        )

    def update_linked_items_from_external_resource(self, resource):
        for r in resource.required_resources:
            if r["model"] == "Performance":
                resource = ExternalResource.objects.filter(
                    id_type=r["id_type"], id_value=r["id_value"]
                ).first()
                if resource and resource.item:
                    self.show = resource.item

    @cached_property
    def crew_by_role(self):
        return _crew_by_role(self.crew)
