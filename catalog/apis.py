from enum import Enum
from typing import List

from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone
from ninja import Schema

from common.api import RedirectedResult, Result, api

from .common import SiteManager
from .models import (
    Album,
    AlbumSchema,
    Edition,
    EditionSchema,
    Game,
    GameSchema,
    Item,
    ItemSchema,
    Movie,
    MovieSchema,
    Performance,
    PerformanceProduction,
    PerformanceProductionSchema,
    PerformanceSchema,
    Podcast,
    PodcastEpisode,
    PodcastEpisodeSchema,
    PodcastSchema,
    TVEpisode,
    TVEpisodeSchema,
    TVSeason,
    TVSeasonSchema,
    TVShow,
    TVShowSchema,
)
from .search.models import enqueue_fetch, get_fetch_lock, query_index

PAGE_SIZE = 20


class SearchResult(Schema):
    data: List[
        EditionSchema
        | MovieSchema
        | TVShowSchema
        | TVSeasonSchema
        | AlbumSchema
        | PodcastSchema
        | GameSchema
        | PerformanceSchema
        | PodcastEpisodeSchema
    ]
    pages: int
    count: int


class EpisodeList(Schema):
    data: List[PodcastEpisodeSchema]
    pages: int
    count: int


class SearchableItemCategory(Enum):
    Book = "book"
    Movie = "movie"
    TV = "tv"
    Movie_And_TV = "movie,tv"
    Music = "music"
    Game = "game"
    Podcast = "podcast"
    Performance = "performance"


class Gallery(Schema):
    name: str
    items: List[ItemSchema]


@api.get(
    "/catalog/search",
    response={200: SearchResult, 400: Result},
    summary="Search items in catalog",
    auth=None,
    tags=["catalog"],
)
def search_item(
    request, query: str, category: SearchableItemCategory | None = None, page: int = 1
):
    """
    Search items in catalog

    count and pages are estimated, the actual data may be less

    unlike the web search, this does not show external results,
    nor does it parse a url to fetch an item. to do that, use /catalog/fetch.
    """
    query = query.strip()
    if not query:
        return 400, {"message": "Invalid query"}
    categories = category.value.split(",") if category else None
    items, num_pages, count, _ = query_index(
        query,
        page=page,
        categories=categories,
        prepare_external=False,
    )
    return 200, {"data": items, "pages": num_pages, "count": count}


@api.get(
    "/catalog/fetch",
    response={200: ItemSchema, 202: Result, 404: Result},
    summary="Fetch item from URL of a supported site",
    auth=None,
    tags=["catalog"],
)
def fetch_item(request, url: str):
    """
    Convert a URL from a supported site (e.g. https://m.imdb.com/title/tt2852400/) to an item.

    If the item is not available in the catalog, HTTP 202 will be returned.
    Wait 15 seconds or longer, call with same input again, it may return the actual fetched item.
    Some site may take ~90 seconds to fetch.
    If not getting the item after 120 seconds, please stop and consider the URL is not available.
    """
    site = SiteManager.get_site_by_url(url)
    if not site:
        return 404, {"message": "URL not supported"}
    item = site.get_item()
    if item:
        return 200, item
    if get_fetch_lock(request.user, url):
        enqueue_fetch(url, False)
    return 202, {"message": "Fetch in progress"}


@api.get(
    "/catalog/gallery/",
    response={200: list[Gallery]},
    summary="Trending items in catalog",
    auth=None,
    tags=["catalog"],
    deprecated=True,
)
def trending_items(request):
    """
    Returns a list of galleries, each gallery is a list of items, deprecated, removing by May 1 2025
    """
    gallery_list = cache.get("public_gallery", [])

    # rotate every 6 minutes
    rot = timezone.now().minute // 6
    for gallery in gallery_list:
        items = cache.get(gallery["name"], [])
        i = rot * len(items) // 10
        gallery["items"] = items[i:] + items[:i]
    return 200, gallery_list


def _get_trending(name):
    rot = timezone.now().minute // 6
    items = cache.get(name, [])
    i = rot * len(items) // 10
    return items[i:] + items[:i]


@api.get(
    "/catalog/trending/book/",
    response={200: list[ItemSchema]},
    summary="Trending books in catalog",
    auth=None,
    tags=["catalog"],
)
def trending_book(request):
    return _get_trending("trending_book")


@api.get(
    "/catalog/trending/movie/",
    response={200: list[ItemSchema]},
    summary="Trending movies in catalog",
    auth=None,
    tags=["catalog"],
)
def trending_movie(request):
    return _get_trending("trending_movie")


@api.get(
    "/catalog/trending/tv/",
    response={200: list[ItemSchema]},
    summary="Trending tv in catalog",
    auth=None,
    tags=["catalog"],
)
def trending_tv(request):
    return _get_trending("trending_tv")


@api.get(
    "/catalog/trending/music/",
    response={200: list[ItemSchema]},
    summary="Trending music in catalog",
    auth=None,
    tags=["catalog"],
)
def trending_music(request):
    return _get_trending("trending_music")


@api.get(
    "/catalog/trending/game/",
    response={200: list[ItemSchema]},
    summary="Trending games in catalog",
    auth=None,
    tags=["catalog"],
)
def trending_game(request):
    return _get_trending("trending_game")


@api.get(
    "/catalog/trending/podcast/",
    response={200: list[ItemSchema]},
    summary="Trending podcasts in catalog",
    auth=None,
    tags=["catalog"],
)
def trending_podcast(request):
    return _get_trending("trending_podcast")


def _get_item(cls, uuid, response):
    item = Item.get_by_url(uuid)
    if not item:
        return 404, {"message": "Item not found"}
    if item.merged_to_item:
        response["Location"] = item.merged_to_item.api_url
        return 302, {"message": "Item merged", "url": item.merged_to_item.api_url}
    if item.is_deleted:
        return 404, {"message": "Item not found"}
    if item.__class__ != cls:
        response["Location"] = item.api_url
        return 302, {"message": "Item recasted", "url": item.api_url}
    return item


@api.get(
    "/book/{uuid}",
    response={200: EditionSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_book(request, uuid: str, response: HttpResponse):
    return _get_item(Edition, uuid, response)


@api.get(
    "/movie/{uuid}",
    response={200: MovieSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_movie(request, uuid: str, response: HttpResponse):
    return _get_item(Movie, uuid, response)


@api.get(
    "/tv/{uuid}",
    response={200: TVShowSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_tv_show(request, uuid: str, response: HttpResponse):
    return _get_item(TVShow, uuid, response)


@api.get(
    "/tv/season/{uuid}",
    response={200: TVSeasonSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_tv_season(request, uuid: str, response: HttpResponse):
    return _get_item(TVSeason, uuid, response)


@api.get(
    "/tv/episode/{uuid}",
    response={200: TVEpisodeSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_tv_episode(request, uuid: str, response: HttpResponse):
    return _get_item(TVEpisode, uuid, response)


@api.get(
    "/podcast/{uuid}",
    response={200: PodcastSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_podcast(request, uuid: str, response: HttpResponse):
    return _get_item(Podcast, uuid, response)


@api.get(
    "/podcast/episode/{uuid}",
    response={200: PodcastEpisodeSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_podcast_episode(request, uuid: str, response: HttpResponse):
    return _get_item(PodcastEpisode, uuid, response)


@api.get(
    "/podcast/{uuid}/episode/",
    response={200: EpisodeList, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_episodes_in_podcast(
    request, uuid: str, response: HttpResponse, page: int = 1, guid: str | None = None
):
    podcast = _get_item(Podcast, uuid, response)
    if not isinstance(podcast, Podcast):
        return podcast
    episodes = podcast.child_items.filter(is_deleted=False, merged_to_item=None)
    if guid:
        episodes = episodes.filter(guid=guid)
    r = {
        "data": list(episodes)[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
        "pages": (episodes.count() + PAGE_SIZE - 1) // PAGE_SIZE,
        "count": episodes.count(),
    }
    return r


@api.get(
    "/album/{uuid}",
    response={200: AlbumSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_album(request, uuid: str, response: HttpResponse):
    return _get_item(Album, uuid, response)


@api.get(
    "/game/{uuid}",
    response={200: GameSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_game(request, uuid: str, response: HttpResponse):
    return _get_item(Game, uuid, response)


@api.get(
    "/performance/{uuid}",
    response={200: PerformanceSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_performance(request, uuid: str, response: HttpResponse):
    return _get_item(Performance, uuid, response)


@api.get(
    "/performance/production/{uuid}",
    response={200: PerformanceProductionSchema, 302: RedirectedResult, 404: Result},
    auth=None,
    tags=["catalog"],
)
def get_performance_production(request, uuid: str, response: HttpResponse):
    return _get_item(PerformanceProduction, uuid, response)
