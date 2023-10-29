from django.urls import path, re_path

from .views import *

app_name = "common"
urlpatterns = [
    path("", home),
    path("home/", home, name="home"),
    path("me/", me, name="me"),
    path("nodeinfo/2.0/", nodeinfo2),
    re_path("^~neodb~(?P<uri>.+)", ap_redirect),
]
