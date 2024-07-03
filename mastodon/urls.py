from django.urls import path

from .views import *

app_name = "mastodon"
urlpatterns = [
    # Mastodon
    path("login/oauth", mastodon_oauth, name="oauth"),
    path("mastodon/login", mastodon_login, name="login"),
    path("mastodon/reconnect", mastodon_reconnect, name="reconnect"),
    path("mastodon/disconnect", mastodon_disconnect, name="mastodon_disconnect"),
    # Email
    path("email/login", email_login, name="email_login"),
    path("email/verify", email_verify, name="email_verify"),
    # Threads
    path("threads/login", threads_login, name="threads_login"),
    path("threads/oauth", threads_oauth, name="threads_oauth"),
    path("threads/reconnect", threads_reconnect, name="threads_reconnect"),
    path("threads/disconnect", threads_disconnect, name="threads_disconnect"),
    path("threads/uninstall", threads_uninstall, name="threads_uninstall"),
    path("threads/delete", threads_delete, name="threads_delete"),
    # Bluesky
]
