"""
Site and SiteManager

Site should inherite from AbstractSite
a Site should map to a unique set of url patterns.
a Site may scrape a url and store result in ResourceContent
ResourceContent persists as an ExternalResource which may link to an Item
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Type, TypeVar

import django_rq
import requests
from loguru import logger
from validators import url as url_validate

from .models import ExternalResource, IdealIdTypes, IdType, Item, SiteName


@dataclass
class ResourceContent:
    lookup_ids: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    cover_image: bytes | None = None
    cover_image_extention: str | None = None

    def dict(self):
        return {"metadata": self.metadata, "lookup_ids": self.lookup_ids}

    def to_json(self) -> str:
        return json.dumps({"metadata": self.metadata, "lookup_ids": self.lookup_ids})


class AbstractSite:
    """
    Abstract class to represent a site
    """

    SITE_NAME: SiteName
    ID_TYPE: IdType | None = None
    WIKI_PROPERTY_ID: str | None = "P0undefined0"
    DEFAULT_MODEL: Type[Item] | None = None
    URL_PATTERNS = [r"\w+://undefined/(\d+)"]

    @classmethod
    def validate_url(cls, url: str):
        u = next(
            iter([re.match(p, url) for p in cls.URL_PATTERNS if re.match(p, url)]),
            None,
        )
        return u is not None

    @classmethod
    def validate_url_fallback(cls, url: str) -> bool:
        return False

    @classmethod
    def id_to_url(cls, id_value: str):
        return "https://undefined/" + id_value

    @classmethod
    def url_to_id(cls, url: str):
        u = next(
            iter([re.match(p, url) for p in cls.URL_PATTERNS if re.match(p, url)]),
            None,
        )
        return u[1] if u else None

    def __str__(self):
        return f"<{self.__class__.__name__}: {self.url}>"

    def __init__(self, url=None, id_value=None):
        # use id if possible, url will be cleaned up by id_to_url()
        self.id_value = id_value or (self.url_to_id(url) if url else None)
        self.url = self.id_to_url(self.id_value) if self.id_value else None
        self.resource = None

    def get_resource(self) -> ExternalResource:
        if not self.resource:
            self.resource = ExternalResource.objects.filter(url=self.url).first()
            if self.resource is None:
                self.resource = ExternalResource.objects.filter(
                    id_type=self.ID_TYPE, id_value=self.id_value
                ).first()
            if self.resource is None:
                self.resource = ExternalResource(
                    id_type=self.ID_TYPE, id_value=self.id_value, url=self.url
                )
        return self.resource

    def scrape(self) -> ResourceContent:
        """subclass should implement this, return ResourceContent object"""
        data = ResourceContent()
        return data

    def scrape_additional_data(self):
        pass

    @staticmethod
    def query_str(content, query: str) -> str:
        return content.xpath(query)[0].strip()

    @staticmethod
    def query_list(content, query: str) -> list[str]:
        return list(content.xpath(query))

    @classmethod
    def match_existing_item_for_resource(
        cls, resource: ExternalResource
    ) -> Item | None:
        """
        try match an existing Item for a given ExternalResource

        order of matching:
        1. look for other ExternalResource by url in prematched_resources, if found, return the item
        2. look for Item by primary_lookup_id_type and primary_lookup_id_value

        """
        for resource_link in resource.prematched_resources:
            url = resource_link.get("url")
            if url:
                matched_resource = ExternalResource.objects.filter(url=url).first()
                if matched_resource and matched_resource.item:
                    return matched_resource.item
        model = resource.get_item_model(cls.DEFAULT_MODEL)
        if not model:
            return None
        ids = resource.get_lookup_ids(cls.DEFAULT_MODEL)
        for t, v in ids:
            matched = None
            # matched = model.objects.filter(
            #     primary_lookup_id_type=t,
            #     primary_lookup_id_value=v,
            #     title=resource.metadata["title"],
            # ).first()
            # if matched is None and resource.id_type not in [
            #     IdType.DoubanMusic,  # DoubanMusic has many dirty data with same UPC
            #     # IdType.Goodreads,  # previous scraper generated some dirty data
            # ]:
            matched = model.objects.filter(
                primary_lookup_id_type=t, primary_lookup_id_value=v
            ).first()
            if matched is None:
                matched = model.objects.filter(
                    primary_lookup_id_type=resource.id_type,
                    primary_lookup_id_value=resource.id_value,
                ).first()
            if matched and matched.merged_to_item:
                matched = matched.merged_to_item
            if (
                matched
                and matched.primary_lookup_id_type not in IdealIdTypes
                and t in IdealIdTypes
            ):
                matched.primary_lookup_id_type = t
                matched.primary_lookup_id_value = v
                matched.save()
            if matched:
                return matched

    @classmethod
    def match_or_create_item_for_resource(cls, resource):
        try:
            previous_item = resource.item
        except Item.DoesNotExist:
            previous_item = None
        resource.item = cls.match_existing_item_for_resource(resource) or previous_item
        if resource.item is None:
            model = resource.get_item_model(cls.DEFAULT_MODEL)
            if not model:
                return None
            t, v = model.get_best_lookup_id(resource.get_all_lookup_ids())
            obj = model.copy_metadata(resource.metadata)
            obj["primary_lookup_id_type"] = t
            obj["primary_lookup_id_value"] = v
            resource.item = model.objects.create(**obj)
        if previous_item != resource.item:
            if previous_item:
                previous_item.log_action({"unmatch": [str(resource), ""]})
            resource.item.log_action({"!match": ["", str(resource)]})
            resource.save(update_fields=["item"])
        return resource.item

    def get_item(self):
        p = self.get_resource()
        if not p:
            # raise ValueError(f'resource not available for {self.url}')
            return None
        if not p.ready:
            # raise ValueError(f'resource not ready for {self.url}')
            return None
        return self.match_or_create_item_for_resource(p)

    @property
    def ready(self):
        return bool(self.resource and self.resource.ready)

    def get_resource_ready(
        self,
        auto_save=True,
        auto_create=True,
        auto_link=True,
        preloaded_content=None,
        ignore_existing_content=False,
    ) -> ExternalResource | None:
        """
        Returns an ExternalResource in scraped state if possible

        Parameters
        ----------
        auto_save : bool
            automatically saves the ExternalResource and, if auto_create, the Item too
        auto_create : bool
            automatically creates an Item if not exist yet
        auto_link : bool
            automatically scrape the linked resources (e.g. a TVSeason may have a linked TVShow)
        preloaded_content : ResourceContent or dict
            skip scrape(), and use this as scraped result
        ignore_existing_content : bool
            if ExternalResource already has content, ignore that and either use preloaded_content or call scrape()
        """
        if auto_link:
            auto_create = True
        if auto_create:
            auto_save = True
        p = self.get_resource()
        resource_content = {}
        if not self.resource:
            return None
        if not p.ready or ignore_existing_content:
            if isinstance(preloaded_content, ResourceContent):
                resource_content = preloaded_content
            elif isinstance(preloaded_content, dict):
                resource_content = ResourceContent(**preloaded_content)
            else:
                resource_content = self.scrape()
            if resource_content:
                p.update_content(resource_content)
        if not p.ready:
            logger.error(f"unable to get resource {self.url} ready")
            return None
        if auto_create:  # and (p.item is None or p.item.is_deleted):
            self.get_item()
        if auto_save:
            p.save()
            if p.item:
                p.item.merge_data_from_external_resources(ignore_existing_content)
                p.item.save()
                self.scrape_additional_data()
        if auto_link:
            for linked_resource in p.required_resources:
                linked_url = linked_resource.get("url")
                if linked_url:
                    linked_site = SiteManager.get_site_by_url(linked_url)
                    if linked_site:
                        linked_site.get_resource_ready(
                            auto_link=False,
                            preloaded_content=linked_resource.get("content"),
                        )
                    else:
                        logger.error(f"unable to get site for {linked_url}")
            if p.related_resources or p.prematched_resources:
                django_rq.get_queue("crawl").enqueue(crawl_related_resources_task, p.pk)
            if p.item:
                p.item.update_linked_items_from_external_resource(p)
                p.item.save()
        return p


T = TypeVar("T", bound=AbstractSite)


class SiteManager:
    registry = {}

    @staticmethod
    def register(target: Type[T]) -> Type[T]:
        id_type = target.ID_TYPE
        if id_type in SiteManager.registry:
            raise ValueError(f"Site for {id_type} already exists")
        SiteManager.registry[id_type] = target
        return target

    @staticmethod
    def get_site_cls_by_id_type(typ: str) -> AbstractSite:
        if typ in SiteManager.registry:
            return SiteManager.registry[typ]
        else:
            raise ValueError(f"Site for {typ} not found")

    @staticmethod
    def get_site_by_url(url: str) -> AbstractSite | None:
        if not url or not url_validate(
            url,
            skip_ipv6_addr=True,
            skip_ipv4_addr=True,
            may_have_port=False,
            strict_query=False,
        ):
            return None
        cls = next(
            filter(lambda p: p.validate_url(url), SiteManager.registry.values()), None
        )
        if cls is None and re.match(r"^https?://(spotify.link|t.co)/.+", url):
            try:
                url2 = requests.head(url, allow_redirects=True, timeout=1).url
                if url2 != url:
                    cls = next(
                        filter(
                            lambda p: p.validate_url(url2),
                            SiteManager.registry.values(),
                        ),
                        None,
                    )
                    if cls:
                        url = url2
            except Exception:
                pass
        if cls is None:
            cls = next(
                filter(
                    lambda p: p.validate_url_fallback(url),
                    SiteManager.registry.values(),
                ),
                None,
            )
        return cls(url) if cls else None

    @staticmethod
    def get_site_by_id(id_type: IdType | str, id_value: str) -> AbstractSite | None:
        if id_type not in SiteManager.registry:
            return None
        cls = SiteManager.registry[id_type]
        return cls(id_value=id_value)

    @staticmethod
    def get_all_sites():
        return SiteManager.registry.values()


def crawl_related_resources_task(resource_pk):
    resource = ExternalResource.objects.filter(pk=resource_pk).first()
    if not resource:
        logger.warning(f"crawl resource not found {resource_pk}")
        return
    links = (resource.related_resources or []) + (resource.prematched_resources or [])
    for w in links:
        try:
            item = None
            site = None
            if w.get("id_value") and w.get("id_type"):
                site = SiteManager.get_site_by_id(w["id_type"], w["id_value"])
            if not site and w.get("url"):
                site = SiteManager.get_site_by_url(w["url"])
            if site:
                res = site.get_resource_ready(
                    ignore_existing_content=False, auto_link=True
                )
                item = site.get_item()
                if item and res and w in resource.prematched_resources:
                    item.merge_data_from_external_resource(res)
            if item:
                logger.info(f"crawled {w} {item}")
            else:
                logger.warning(f"crawl {w} failed")
        except Exception as e:
            logger.warning(f"crawl {w} error {e}")
