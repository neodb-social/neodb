from django.urls import path, re_path

from .views import *

app_name = "social"
urlpatterns = [
    path("", feed, name="feed"),
    path("focus", focus, name="focus"),
    path("data", data, name="data"),
    path("notification", notification, name="notification"),
    path("events", events, name="events"),
]
