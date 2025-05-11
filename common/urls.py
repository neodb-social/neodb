from django.urls import path, re_path

from .views import *

app_name = "common"
urlpatterns = [
    path("", home),
    path("search", search, name="search"),
    path("home/", home, name="home"),
    path("site/share", share, name="share"),
    path("site/manifest.json", manifest, name="manifest"),
    path("site/opensearch.xml", opensearch, name="opensearch"),
    path("me/", me, name="me"),
    path("nodeinfo/<str:version>/", nodeinfo2),
    path("developer/", console, name="developer"),
    path("auth/signup/", signup, name="signup"),
    path("auth/signup/<str:code>/", signup, name="signup"),
    re_path("^~neodb~(?P<uri>.+)", ap_redirect),
]
