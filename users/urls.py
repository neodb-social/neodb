from django.urls import path

from .views import *

app_name = "users"
urlpatterns = [
    path("login", login, name="login"),
    path("register", register, name="register"),
    path("fetch_refresh", fetch_refresh, name="fetch_refresh"),
    path("data", data, name="data"),
    path("info", account_info, name="info"),
    path("profile", account_profile, name="profile"),
    path("data/import/status", data_import_status, name="import_status"),
    path("data/import/goodreads", import_goodreads, name="import_goodreads"),
    path("data/import/douban", import_douban, name="import_douban"),
    path("data/import/letterboxd", import_letterboxd, name="import_letterboxd"),
    path("data/import/opml", import_opml, name="import_opml"),
    path("data/export/reviews", export_reviews, name="export_reviews"),
    path("data/export/marks", export_marks, name="export_marks"),
    path("data/sync_mastodon", sync_mastodon, name="sync_mastodon"),
    path(
        "data/sync_mastodon_preference",
        sync_mastodon_preference,
        name="sync_mastodon_preference",
    ),
    path("data/reset_visibility", reset_visibility, name="reset_visibility"),
    path("data/clear_data", clear_data, name="clear_data"),
    path("preferences", preferences, name="preferences"),
    path("logout", logout, name="logout"),
    path("layout", set_layout, name="set_layout"),
    path("follow/<str:user_name>", follow, name="follow"),
    path("unfollow/<str:user_name>", unfollow, name="unfollow"),
    path(
        "accept_follow_request/<str:user_name>",
        accept_follow_request,
        name="accept_follow_request",
    ),
    path(
        "reject_follow_request/<str:user_name>",
        reject_follow_request,
        name="reject_follow_request",
    ),
    path("mute/<str:user_name>", mute, name="mute"),
    path("unmute/<str:user_name>", unmute, name="unmute"),
    path("block/<str:user_name>", block, name="block"),
    path("unblock/<str:user_name>", unblock, name="unblock"),
    path(
        "mark_announcements_read/",
        mark_announcements_read,
        name="mark_announcements_read",
    ),
    path(
        "announcements/",
        announcements,
        name="announcements",
    ),
]
