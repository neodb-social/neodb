from auditlog.registry import auditlog

from .book import Edition, EditionInSchema, EditionSchema, Series, Work
from .collection import Collection as CatalogCollection
from .game import Game, GameInSchema, GameSchema
from .item import (
    AvailableItemCategory,
    ExternalResource,
    IdealIdTypes,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    ItemSchema,
    LookupIdDescriptor,
    PrimaryLookupIdDescriptor,
    SiteName,
    item_categories,
    item_content_types,
)
from .movie import Movie, MovieInSchema, MovieSchema
from .music import Album, AlbumInSchema, AlbumSchema
from .performance import (
    Performance,
    PerformanceProduction,
    PerformanceProductionSchema,
    PerformanceSchema,
)
from .podcast import (
    Podcast,
    PodcastEpisode,
    PodcastEpisodeSchema,
    PodcastInSchema,
    PodcastSchema,
)
from .tv import (
    TVEpisode,
    TVEpisodeSchema,
    TVSeason,
    TVSeasonInSchema,
    TVSeasonSchema,
    TVShow,
    TVShowInSchema,
    TVShowSchema,
)

from ..search.models import ExternalSearchResultItem  # isort:skip
from ..index import CatalogIndex, CatalogQueryParser, CatalogSearchResult

# class Exhibition(Item):

#     class Meta:
#         proxy = True


# class Fanfic(Item):

#     class Meta:
#         proxy = True


def init_catalog_audit_log():
    for cls in Item.__subclasses__():
        auditlog.register(
            cls,
            exclude_fields=[
                "id",
                "item_ptr",
                "polymorphic_ctype",
                "metadata",
                "created_time",
                "edited_time",
                # related fields are not supported in django-auditlog yet
                "lookup_ids",
                "external_resources",
                "merged_from_items",
                "focused_comments",
            ],
        )

    auditlog.register(
        ExternalResource, include_fields=["item", "id_type", "id_value", "url"]
    )

    # logger.debug(f"Catalog audit log initialized for {item_content_types().values()}")


__all__ = [
    "IdType",
    "SiteName",
    "ItemCategory",
    "PrimaryLookupIdDescriptor",
    "LookupIdDescriptor",
    "AvailableItemCategory",
    "Item",
    "ExternalResource",
    "CatalogCollection",
    "AvailableItemCategory",
    "ExternalResource",
    "ExternalSearchResultItem",
    "IdType",
    "IdealIdTypes",
    "Item",
    "ItemCategory",
    "ItemInSchema",
    "ItemSchema",
    "SiteName",
    "item_categories",
    "item_content_types",
    "Edition",
    "EditionInSchema",
    "EditionSchema",
    "Series",
    "Work",
    "Game",
    "GameInSchema",
    "GameSchema",
    "Movie",
    "MovieInSchema",
    "MovieSchema",
    "Album",
    "AlbumInSchema",
    "AlbumSchema",
    "Performance",
    "PerformanceProduction",
    "PerformanceProductionSchema",
    "PerformanceSchema",
    "Podcast",
    "PodcastEpisode",
    "PodcastEpisodeSchema",
    "PodcastInSchema",
    "PodcastSchema",
    "TVEpisode",
    "TVEpisodeSchema",
    "TVSeason",
    "TVSeasonInSchema",
    "TVSeasonSchema",
    "TVShow",
    "TVShowInSchema",
    "TVShowSchema",
    "CatalogIndex",
    "CatalogQueryParser",
    "CatalogSearchResult",
    "init_catalog_audit_log",
]
