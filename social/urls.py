from django.urls import path

from .views import *

app_name = "social"
urlpatterns = [
    path("", feed, name="feed"),
    path("focus", focus, name="focus"),
    path("data", data, name="data"),
    path("search_data", search_data, name="search_data"),
    path("notification", notification, name="notification"),
    path("dismiss_notification", dismiss_notification, name="dismiss_notification"),
    path("events", events, name="events"),
]
