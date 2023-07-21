from django.urls import path, re_path

from .views import *

app_name = "common"
urlpatterns = [
    path("", home),
    path("home/", home, name="home"),
    path("me/", me, name="me"),
    re_path("^~neodb~(?P<uri>.+)", ap_redirect),
]
