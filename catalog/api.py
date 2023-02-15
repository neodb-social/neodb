from .models import *
from .common import *
from .sites import *
from ninja import Schema
from django.http import Http404
from common.api import api


class SearchResult(Schema):
    code: int
    items: list[ItemSchema]


@api.post("/catalog/search", response=SearchResult)
def search_item(request, query: str, category: ItemCategory | None = None):
    query = query.strip()
    if not query:
        return {"code": -1, "items": []}
    result = Indexer.search(query, page=1, category=category)
    return {"code": 0, "items": result.items}


@api.post("/catalog/fetch", response=ItemSchema)
def fetch_item(request, url: str):
    site = SiteManager.get_site_by_url(url)
    if not site:
        return Http404()
    resource = site.get_resource_ready()
    if not resource:
        return Http404()
    return site.get_item()


@api.get("/book/{uuid}/", response=EditionSchema)
def get_edition(request, uuid: str):
    item = Edition.get_by_url(uuid)
    if not item:
        raise Http404(uuid)
    return item


@api.get("/movie/{uuid}/", response=MovieSchema)
def get_movie(request, uuid: str):
    item = Movie.get_by_url(uuid)
    if not item:
        raise Http404(uuid)
    return item


@api.get("/tvshow/{uuid}/", response=TVShowSchema)
def get_tvshow(request, uuid: str):
    item = TVShow.get_by_url(uuid)
    if not item:
        raise Http404(uuid)
    return item


@api.get("/tvseason/{uuid}/", response=TVSeasonSchema)
def get_tvseason(request, uuid: str):
    item = TVSeason.get_by_url(uuid)
    if not item:
        raise Http404(uuid)
    return item


@api.get("/podcast/{uuid}/", response=PodcastSchema)
def get_podcast(request, uuid: str):
    item = Podcast.get_by_url(uuid)
    if not item:
        raise Http404(uuid)
    return item


@api.get("/album/{uuid}/", response=AlbumSchema)
def get_album(request, uuid: str):
    item = Album.get_by_url(uuid)
    if not item:
        raise Http404(uuid)
    return item


@api.get("/game/{uuid}/", response=GameSchema)
def get_game(request, uuid: str):
    item = Game.get_by_url(uuid)
    if not item:
        raise Http404(uuid)
    return item


# @api.get("/book", response=List[EditionSchema])
# def list_editions(request):
#     qs = Edition.objects.all()
#     return qs


# @api.post("/book/")
# def create_edition(request, payload: EditionInSchema):
#     edition = Edition.objects.create(**payload.dict())
#     return {"id": edition.uuid}


# @api.put("/book/{uuid}/")
# def update_edition(request, uuid: str, payload: EditionInSchema):
#     edition = get_object_or_404(Item, uid=base62.decode(uuid))
#     for attr, value in payload.dict().items():
#         setattr(edition, attr, value)
#     edition.save()
#     return {"success": True}


# @api.delete("/book/{uuid}/")
# def delete_edition(request, uuid: str):
#     edition = get_object_or_404(Edition, uid=base62.decode(uuid))
#     edition.delete()
#     return {"success": True}
