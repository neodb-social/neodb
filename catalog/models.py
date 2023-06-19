from .common.models import (
    ExternalResource,
    Item,
    ItemSchema,
    item_content_types,
    item_categories,
)
from .book.models import Edition, Work, Series, EditionSchema, EditionInSchema
from .movie.models import Movie, MovieSchema, MovieInSchema
from .tv.models import (
    TVShow,
    TVSeason,
    TVEpisode,
    TVShowSchema,
    TVShowInSchema,
    TVSeasonSchema,
    TVSeasonInSchema,
)
from .music.models import Album, AlbumSchema, AlbumInSchema
from .game.models import Game, GameSchema, GameInSchema
from .podcast.models import Podcast, PodcastSchema, PodcastInSchema, PodcastEpisode
from .performance.models import (
    Performance,
    PerformanceProduction,
    PerformanceSchema,
    PerformanceProductionSchema,
)
from .collection.models import Collection as CatalogCollection
from .search.models import Indexer
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
import logging
from auditlog.registry import auditlog

_logger = logging.getLogger(__name__)


# class Exhibition(Item):

#     class Meta:
#         proxy = True


# class Fanfic(Item):

#     class Meta:
#         proxy = True


def init_catalog_search_models():
    if settings.DISABLE_MODEL_SIGNAL:
        _logger.warn(
            "Catalog models are not being indexed with DISABLE_MODEL_SIGNAL configuration"
        )
        return
    # skip indexing if the item type should never show up in search
    Indexer.update_model_indexable(Edition)
    # Indexer.update_model_indexable(Work)
    Indexer.update_model_indexable(Movie)
    Indexer.update_model_indexable(TVShow)
    Indexer.update_model_indexable(TVSeason)
    Indexer.update_model_indexable(Album)
    Indexer.update_model_indexable(Game)
    Indexer.update_model_indexable(Podcast)
    Indexer.update_model_indexable(Performance)
    # Indexer.update_model_indexable(PerformanceProduction)
    # Indexer.update_model_indexable(CatalogCollection)


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
                "last_editor",
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

    # _logger.debug(f"Catalog audit log initialized for {item_content_types().values()}")
