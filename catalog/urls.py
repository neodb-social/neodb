from django.urls import path, re_path
from .views import *
from .models import *

app_name = "catalog"


def _get_all_url_paths():
    paths = ["item"]
    for cls in Item.__subclasses__():
        p = getattr(cls, "url_path", None)
        if p:
            paths.append(p)
    res = "|".join(paths)
    return res


urlpatterns = [
    re_path(
        r"^item/(?P<item_uid>[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12})$",
        retrieve_by_uuid,
        name="retrieve_by_uuid",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/embed$",
        embed,
        name="embed",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})$",
        retrieve,
        name="retrieve",
    ),
    path("podcast/<str:item_uuid>/episodes", episode_data, name="episode_data"),
    path("catalog/create/<str:item_model>", create, name="create"),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/edit$",
        edit,
        name="edit",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/delete$",
        delete,
        name="delete",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/assign_parent$",
        assign_parent,
        name="assign_parent",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/remove_unused_seasons$",
        remove_unused_seasons,
        name="remove_unused_seasons",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/merge$",
        merge,
        name="merge",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/recast$",
        recast,
        name="recast",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/comments",
        comments,
        name="comments",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/reviews",
        reviews,
        name="reviews",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/review_list",
        review_list,
        name="review_list",
    ),
    re_path(
        r"^(?P<item_path>"
        + _get_all_url_paths()
        + ")/(?P<item_uuid>[A-Za-z0-9]{21,22})/marks(?:/(?P<following_only>\\w+))?",
        mark_list,
        name="mark_list",
    ),
    path("search/", search, name="search"),
    path("search/external/", external_search, name="external_search"),
    path("fetch_refresh/<str:job_id>", fetch_refresh, name="fetch_refresh"),
    path("refetch", refetch, name="refetch"),
    path("unlink", unlink, name="unlink"),
    path("discover/", discover, name="discover"),
]
