from django.urls import path, re_path
from .views import *
from .feeds import ReviewFeed
from catalog.models import *


app_name = "journal"


def _get_all_categories():
    res = "|".join(item_categories().keys())
    return res


def _get_all_shelf_types():
    return "|".join(ShelfType.values)


urlpatterns = [
    path("wish/<str:item_uuid>", wish, name="wish"),
    path("like/<str:piece_uuid>", like, name="like"),
    path("unlike/<str:piece_uuid>", unlike, name="unlike"),
    path("mark/<str:item_uuid>", mark, name="mark"),
    path("comment/<str:item_uuid>", comment, name="comment"),
    path("mark_log/<str:item_uuid>/<str:log_id>", mark_log, name="mark_log"),
    path("mark_history/<str:item_uuid>", mark_history, name="mark_history"),
    path("mark_history/edit/<str:item_uuid>", mark_history_edit, name="mark_history_edit"),
    path(
        "add_to_collection/<str:item_uuid>", add_to_collection, name="add_to_collection"
    ),
    path("review/<str:review_uuid>", review_retrieve, name="review_retrieve"),
    path("review/create/<str:item_uuid>/", review_edit, name="review_create"),
    path(
        "review/edit/<str:item_uuid>/<str:review_uuid>", review_edit, name="review_edit"
    ),
    path("review/delete/<str:piece_uuid>", piece_delete, name="review_delete"),
    path(
        "collection/<str:collection_uuid>",
        collection_retrieve,
        name="collection_retrieve",
    ),
    path("collection/create/", collection_edit, name="collection_create"),
    path(
        "collection/edit/<str:collection_uuid>", collection_edit, name="collection_edit"
    ),
    path("collection/delete/<str:piece_uuid>", piece_delete, name="collection_delete"),
    path(
        "collection/share/<str:collection_uuid>",
        collection_share,
        name="collection_share",
    ),
    path(
        "collection/<str:collection_uuid>/items",
        collection_retrieve_items,
        name="collection_retrieve_items",
    ),
    path(
        "collection/<str:collection_uuid>/append_item",
        collection_append_item,
        name="collection_append_item",
    ),
    path(
        "collection/<str:collection_uuid>/remove_item/<str:item_uuid>",
        collection_remove_item,
        name="collection_remove_item",
    ),
    path(
        "collection/<str:collection_uuid>/collection_update_member_order",
        collection_update_member_order,
        name="collection_update_member_order",
    ),
    path(
        "collection/<str:collection_uuid>/update_item_note/<str:item_uuid>",
        collection_update_item_note,
        name="collection_update_item_note",
    ),
    path(
        "collection/<str:collection_uuid>/add_featured",
        collection_add_featured,
        name="collection_add_featured",
    ),
    path(
        "collection/<str:collection_uuid>/remove_featured",
        collection_remove_featured,
        name="collection_remove_featured",
    ),
    re_path(
        r"^users/(?P<user_name>[~A-Za-z0-9_\-.@]+)/(?P<shelf_type>"
        + _get_all_shelf_types()
        + ")/(?P<item_category>"
        + _get_all_categories()
        + ")/$",
        user_mark_list,
        name="user_mark_list",
    ),
    re_path(
        r"^users/(?P<user_name>[~A-Za-z0-9_\-.@]+)/reviews/(?P<item_category>"
        + _get_all_categories()
        + ")/$",
        user_review_list,
        name="user_review_list",
    ),
    re_path(
        r"^users/(?P<user_name>[~A-Za-z0-9_\-.@]+)/tags/(?P<tag_title>.+)/$",
        user_tag_member_list,
        name="user_tag_member_list",
    ),
    path(
        "tag/edit",
        user_tag_edit,
        name="user_tag_edit",
    ),
    re_path(
        r"^users/(?P<user_name>[~A-Za-z0-9_\-.@]+)/collections/$",
        user_collection_list,
        name="user_collection_list",
    ),
    re_path(
        r"^users/(?P<user_name>[~A-Za-z0-9_\-.@]+)/like/collections/$",
        user_liked_collection_list,
        name="user_liked_collection_list",
    ),
    re_path(
        r"^users/(?P<user_name>[~A-Za-z0-9_\-.@]+)/tags/$",
        user_tag_list,
        name="user_tag_list",
    ),
    re_path(
        r"^users/(?P<user_name>[~A-Za-z0-9_\-.@]+)/$", profile, name="user_profile"
    ),
    re_path(
        r"^users/(?P<user_name>[~A-Za-z0-9_\-.@]+)/calendar_data$",
        user_calendar_data,
        name="user_calendar_data",
    ),
    path("users/<str:id>/feed/reviews/", ReviewFeed(), name="review_feed"),
]
