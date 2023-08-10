from django.urls import path

from .views import *

app_name = "common"
urlpatterns = [
    path("", home),
    path("home/", home, name="home"),
    path("me/", me, name="me"),
]
