from django.urls import path, re_path

from .views import *

app_name = "social"
urlpatterns = [
    path("", feed, name="feed"),
    path("data", data, name="data"),
]
