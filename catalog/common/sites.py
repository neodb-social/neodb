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
from hashlib import md5
from typing import Type, TypeVar

import django_rq
import requests
from django.conf import settings
from django.core.cache import cache
from loguru import logger
from validators import url as url_validate

from common.models.misc import uniq

from .models import ExternalResource, IdType, Item, SiteName


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
    MATCHABLE_MODELS: list[Type[Item]] = []
    URL_PATTERNS = [r"\w+://undefined/(\d+)"]

    @classmethod
    def check_model_compatibility(cls, model: Type[Item]) -> bool:
        """
        Check if the model is compatible with this site.
        """
        return model in cls.MATCHABLE_MODELS or (
            cls.DEFAULT_MODEL is not None and issubclass(model, cls.DEFAULT_MODEL)
        )

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

    def to_id_str(self) -> str | None:
        if self.ID_TYPE and self.id_value:
            return f"{self.ID_TYPE}:{self.id_value}"

    def __str__(self):
        return f"<{self.__class__.__name__}: {self.url}>"

    def __init__(self, url=None, id_value=None):
        # use id if possible, url will be cleaned up by id_to_url()
        self.id_value = id_value or (self.url_to_id(url) if url else None)
        self.url = self.id_to_url(self.id_value) if self.id_value else None
        self.resource = None

    def clear_cache(self):
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

    # @classmethod
    # async def search_task(
    #     cls, q: str, page: int, category: str, page_size: int
    # ) -> "list[ExternalSearchResultItem]":
    #     # implement this method in subclass to enable external search
    #     return []

    def scrape(self) -> ResourceContent:
        """subclass should implement this, return ResourceContent object"""
        data = ResourceContent()
        return data

    def scrape_additional_data(self) -> bool:
        return False

    @staticmethod
    def query_str(content, query: str) -> str:
        return content.xpath(query)[0].strip()

    @staticmethod
    def query_list(content, query: str) -> list:
        return list(content.xpath(query))

    def get_item(self, ignore_existing_content: bool = False):
        p = self.get_resource()
        if not p:
            # raise ValueError(f'resource not available for {self.url}')
            return None
        if not p.ready:
            # raise ValueError(f'resource not ready for {self.url}')
            return None
        p.match_and_link_item(self.DEFAULT_MODEL, ignore_existing_content)
        return p.item

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
        if auto_save:
            p.save()
        if auto_create:
            self.get_item(ignore_existing_content)
        if auto_save and p.item:
            self.scrape_additional_data()
        if auto_link:
            SiteManager.fetch_linked_resources(
                p, p.required_resources, ExternalResource.LinkType.PARENT
            )
            if p.related_resources or p.other_lookup_ids or p.prematched_resources:
                django_rq.get_queue("crawl").enqueue(
                    SiteManager.fetch_related_resources_task, p.pk
                )
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
    def has_id_type(typ: str) -> bool:
        return typ in SiteManager.registry

    @staticmethod
    def get_site_cls_by_id_type(typ: str) -> type[AbstractSite]:
        if typ in SiteManager.registry:
            return SiteManager.registry[typ]
        else:
            raise ValueError(f"Site for {typ} not found")

    @staticmethod
    def get_redirected_url(url: str, allow_head: bool = True) -> str:
        k = "_redir_" + md5(url.encode()).hexdigest()
        u = cache.get(k, default=None)
        if u == "":
            return url
        elif u:
            return u
        elif not allow_head:
            return url
        try:
            u = requests.head(url, allow_redirects=True, timeout=2).url
        except requests.RequestException:
            logger.warning(f"HEAD timeout: {url}")
            u = url
        cache.set(k, u if u != url else "", 3600)
        return u

    @staticmethod
    def get_class_by_url(url: str) -> Type[AbstractSite] | None:
        return next(
            filter(lambda p: p.validate_url(url), SiteManager.registry.values()), None
        )

    @staticmethod
    def get_fallback_class_by_url(url: str) -> Type[AbstractSite] | None:
        return next(
            filter(
                lambda p: p.validate_url_fallback(url), SiteManager.registry.values()
            ),
            None,
        )

    @staticmethod
    def get_site_by_url_or_id(
        url_or_id: str, detect_redirection: bool = True, detect_fallback: bool = True
    ) -> AbstractSite | None:
        if "://" not in url_or_id:
            i = url_or_id.split(":", 1)
            if len(i) == 2 and i[0] and i[1]:
                return SiteManager.get_site_by_id(i[0], i[1])
        else:
            return SiteManager.get_site_by_url(
                url_or_id, detect_redirection, detect_fallback
            )

    @staticmethod
    def get_site_by_url(
        url: str, detect_redirection: bool = True, detect_fallback: bool = True
    ) -> AbstractSite | None:
        if not url or not url_validate(
            url,
            skip_ipv6_addr=True,
            skip_ipv4_addr=True,
            may_have_port=False,
            strict_query=False,
        ):
            return None
        u = SiteManager.get_redirected_url(url, allow_head=detect_redirection)
        cls = SiteManager.get_class_by_url(u)
        if cls is None and detect_fallback:
            cls = SiteManager.get_fallback_class_by_url(u)
        if cls is None and u != url:
            cls = SiteManager.get_class_by_url(url)
            if cls is None and detect_fallback:
                cls = SiteManager.get_fallback_class_by_url(url)
            if cls:
                u = url
        return cls(u) if cls else None

    @staticmethod
    def get_site_by_id(id_type: IdType | str, id_value: str) -> AbstractSite | None:
        if id_type not in SiteManager.registry:
            return None
        cls = SiteManager.registry[id_type]
        return cls(id_value=id_value)

    @staticmethod
    def get_all_sites():
        return SiteManager.registry.values()

    @staticmethod
    def get_sites_for_search():
        if settings.SEARCH_SITES == ["-"]:
            return []
        sites = SiteManager.get_all_sites()
        if settings.SEARCH_SITES == ["*"] or not settings.SEARCH_SITES:
            return [s for s in sites if hasattr(s, "search_task")]
        ss = {s.SITE_NAME.value: s for s in sites if hasattr(s, "search_task")}
        return [ss[s] for s in settings.SEARCH_SITES if s in ss]

    @classmethod
    def fetch_linked_resources(cls, resource, linked_resources, link_type):
        processed = False
        for linked_resource in linked_resources:
            linked_site = None
            if "url" in linked_resource:
                linked_site = SiteManager.get_site_by_url(linked_resource["url"])
            elif (
                "id_type" in linked_resource
                and linked_resource.get("id_value")
                and linked_resource.get("id_type") in SiteManager.registry
            ):
                linked_site = SiteManager.get_site_by_id(
                    linked_resource["id_type"], linked_resource["id_value"]
                )
            else:
                continue
            if linked_site:
                try:
                    fetched = linked_site.get_resource_ready(
                        auto_link=False,
                        preloaded_content=linked_resource.get("content"),
                    )
                except Exception as e:
                    logger.error(
                        f"error fetching {linked_resource} from {linked_site}: {e}"
                    )
                    continue
                logger.success(f"fetched {resource}'s {link_type}: {fetched}")
                if fetched:
                    match link_type:
                        case ExternalResource.LinkType.PARENT:
                            processed |= resource.process_fetched_resource(
                                fetched, ExternalResource.LinkType.PARENT
                            )
                            if (
                                fetched.process_fetched_resource(
                                    resource, ExternalResource.LinkType.CHILD
                                )
                                and fetched.item
                            ):
                                fetched.item.save()
                        case ExternalResource.LinkType.CHILD:
                            processed |= resource.process_fetched_resource(
                                fetched, ExternalResource.LinkType.CHILD
                            )
                            if (
                                fetched.process_fetched_resource(
                                    resource, ExternalResource.LinkType.PARENT
                                )
                                and fetched.item
                            ):
                                fetched.item.save()
                        case ExternalResource.LinkType.PREMATCHED:
                            processed |= resource.process_fetched_resource(
                                fetched, ExternalResource.LinkType.PREMATCHED
                            )
                        case _:
                            logger.error(f"unknown link type {link_type}")
            else:
                logger.error(f"unable to get site for {linked_resource}")
        if resource.item and processed:
            resource.item.save()

    @staticmethod
    def fetch_related_resources_task(requester_resource_pk):
        resource = ExternalResource.objects.filter(pk=requester_resource_pk).first()
        if not resource:
            logger.error(f"requester resource not found {requester_resource_pk}")
            return
        links = uniq(
            [
                {"id_type": t, "id_value": v}
                for t, v in (resource.other_lookup_ids or {}).items()
            ]
            + (resource.prematched_resources or [])
        )
        if links:
            SiteManager.fetch_linked_resources(
                resource, links, ExternalResource.LinkType.PREMATCHED
            )
        if resource.related_resources:
            SiteManager.fetch_linked_resources(
                resource, resource.related_resources, ExternalResource.LinkType.CHILD
            )
