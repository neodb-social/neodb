from .models import *
from .common import *
from .sites import *
from ninja import Schema
from django.http import Http404
from common.api import *
from .search.views import enqueue_fetch
from django.utils.translation import gettext_lazy as _
from django.db import models
from django.http import HttpRequest, HttpResponse


class SearchResult(Schema):
    items: list[ItemSchema]


@api.get(
    "/catalog/search",
    response={200: SearchResult, 400: Result},
    summary="Search items in catalog",
    auth=None,
)
def search_item(request, query: str, category: AvailableItemCategory | None = None):
    query = query.strip()
    if not query:
        return 400, {"message": "Invalid query"}
    result = Indexer.search(query, page=1, category=category)
    return 200, {"items": result.items}


@api.get(
    "/catalog/fetch",
    response={200: ItemSchema, 202: Result},
    summary="Fetch item from URL of a supported site",
    auth=None,
)
def fetch_item(request, url: str):
    """
    Convert a URL from a supported site (e.g. https://m.imdb.com/title/tt2852400/) to an item.

    If the item is not available in the catalog, HTTP 202 will be returned.
    Wait 10 seconds or longer, call with same input again, it may return the actual fetched item.
    Some site may take ~90 seconds to fetch.
    """
    site = SiteManager.get_site_by_url(url)
    if not site:
        raise Http404(url)
    item = site.get_item()
    if item:
        return 200, item
    enqueue_fetch(url, False)
    return 202, {"message": "Fetch in progress"}


def _get_item(cls, uuid, response):
    item = cls.get_by_url(uuid)
    if not item:
        return 404, {"message": "Item not found"}
    if item.merged_to_item:
        response["Location"] = item.merged_to_item.api_url
        return 302, {"message": "Item merged", "url": item.merged_to_item.api_url}
    if item.is_deleted:
        return 404, {"message": "Item not found"}
    return item


@api.get(
    "/book/{uuid}",
    response={200: EditionSchema, 302: RedirectedResult, 404: Result},
    auth=None,
)
def get_book(request, uuid: str, response: HttpResponse):
    return _get_item(Edition, uuid, response)


@api.get(
    "/movie/{uuid}",
    response={200: MovieSchema, 302: RedirectedResult, 404: Result},
    auth=None,
)
def get_movie(request, uuid: str, response: HttpResponse):
    return _get_item(Movie, uuid, response)


@api.get(
    "/tv/{uuid}",
    response={200: TVShowSchema, 302: RedirectedResult, 404: Result},
    auth=None,
)
def get_tv_show(request, uuid: str, response: HttpResponse):
    return _get_item(TVShow, uuid, response)


@api.get(
    "/tv/season/{uuid}",
    response={200: TVSeasonSchema, 302: RedirectedResult, 404: Result},
    auth=None,
)
def get_tv_season(request, uuid: str, response: HttpResponse):
    return _get_item(TVSeason, uuid, response)


@api.get(
    "/podcast/{uuid}",
    response={200: PodcastSchema, 302: RedirectedResult, 404: Result},
    auth=None,
)
def get_podcast(request, uuid: str, response: HttpResponse):
    return _get_item(Podcast, uuid, response)


@api.get(
    "/album/{uuid}",
    response={200: AlbumSchema, 302: RedirectedResult, 404: Result},
    auth=None,
)
def get_album(request, uuid: str, response: HttpResponse):
    return _get_item(Album, uuid, response)


@api.get(
    "/game/{uuid}",
    response={200: GameSchema, 302: RedirectedResult, 404: Result},
    auth=None,
)
def get_game(request, uuid: str, response: HttpResponse):
    return _get_item(Game, uuid, response)


# Legacy API will be removed soon


@api.post(
    "/catalog/search",
    response={200: SearchResult, 400: Result},
    summary="This method is deprecated, will be removed by Aug 1 2023; use GET instead",
    auth=None,
    deprecated=True,
)
def search_item_legacy(
    request, query: str, category: AvailableItemCategory | None = None
):
    query = query.strip()
    if not query:
        return 400, {"message": "Invalid query"}
    result = Indexer.search(query, page=1, category=category)
    return 200, {"items": result.items}


@api.post(
    "/catalog/fetch",
    response={200: ItemSchema, 202: Result},
    summary="This method is deprecated, will be removed by Aug 1 2023; use GET instead",
    auth=None,
    deprecated=True,
)
def fetch_item_legacy(request, url: str):
    site = SiteManager.get_site_by_url(url)
    if not site:
        raise Http404(url)
    item = site.get_item()
    if item:
        return 200, item
    enqueue_fetch(url, False)
    return 202, {"message": "Fetch in progress"}


@api.get(
    "/movie/{uuid}/",
    response={200: MovieSchema, 302: RedirectedResult, 404: Result},
    summary="This method is deprecated, will be removed by Aug 1 2023",
    auth=None,
    deprecated=True,
)
def get_movie_legacy(request, uuid: str, response: HttpResponse):
    return _get_item(Movie, uuid, response)


@api.get(
    "/tv/{uuid}/",
    response={200: TVShowSchema, 302: RedirectedResult, 404: Result},
    summary="This method is deprecated, will be removed by Aug 1 2023",
    auth=None,
    deprecated=True,
)
def get_tv_show_legacy(request, uuid: str, response: HttpResponse):
    return _get_item(TVShow, uuid, response)


@api.get(
    "/tvseason/{uuid}/",
    response={200: TVSeasonSchema, 302: RedirectedResult, 404: Result},
    summary="This method is deprecated, will be removed by Aug 1 2023",
    auth=None,
    deprecated=True,
)
def get_tv_season_legacy(request, uuid: str, response: HttpResponse):
    return _get_item(TVSeason, uuid, response)


@api.get(
    "/podcast/{uuid}/",
    response={200: PodcastSchema, 302: RedirectedResult, 404: Result},
    summary="This method is deprecated, will be removed by Aug 1 2023",
    auth=None,
    deprecated=True,
)
def get_podcast_legacy(request, uuid: str, response: HttpResponse):
    return _get_item(Podcast, uuid, response)


@api.get(
    "/album/{uuid}/",
    response={200: AlbumSchema, 302: RedirectedResult, 404: Result},
    summary="This method is deprecated, will be removed by Aug 1 2023",
    auth=None,
    deprecated=True,
)
def get_album_legacy(request, uuid: str, response: HttpResponse):
    return _get_item(Album, uuid, response)


@api.get(
    "/game/{uuid}/",
    response={200: GameSchema, 302: RedirectedResult, 404: Result},
    summary="This method is deprecated, will be removed by Aug 1 2023",
    auth=None,
    deprecated=True,
)
def get_game_legacy(request, uuid: str, response: HttpResponse):
    return _get_item(Game, uuid, response)
