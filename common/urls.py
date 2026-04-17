from django.urls import path, re_path

from .views import *
from .views_manage import (
    AccessSettings,
    AdvancedSettings,
    APIKeysSettings,
    BrandingSettings,
    DiscoverSettings,
    DownloaderSettings,
    FederationSettings,
    manage_root,
)

app_name = "common"
urlpatterns = [
    path("", home),
    path("search", search, name="search"),
    path("scan/", scan, name="scan"),
    path("home/", home, name="home"),
    path("site/share", share, name="share"),
    path("site/manifest.json", manifest, name="manifest"),
    path("site/opensearch.xml", opensearch, name="opensearch"),
    path("about/", about, name="about"),
    path("me/", me, name="me"),
    path("nodeinfo/<str:version>/", nodeinfo2),
    path("developer/", console, name="developer"),
    path("auth/signup/", signup, name="signup"),
    path("auth/signup/<str:code>/", signup, name="signup"),
    re_path(r"^\.well-known/oauth-protected-resource", oauth_protected_resource),
    re_path(r"^\.well-known/oauth-authorization-server", oauth_authorization_server),
    re_path(r"^~neodb~(?P<uri>.+)", ap_redirect),
    # Site management
    path("manage/", manage_root, name="manage"),
    path(
        "manage/branding/",
        BrandingSettings.as_view(),
        name="manage_branding",
    ),
    path(
        "manage/discover/",
        DiscoverSettings.as_view(),
        name="manage_discover",
    ),
    path(
        "manage/access/",
        AccessSettings.as_view(),
        name="manage_access",
    ),
    path(
        "manage/federation/",
        FederationSettings.as_view(),
        name="manage_federation",
    ),
    path(
        "manage/api-keys/",
        APIKeysSettings.as_view(),
        name="manage_api_keys",
    ),
    path(
        "manage/downloader/",
        DownloaderSettings.as_view(),
        name="manage_downloader",
    ),
    path(
        "manage/advanced/",
        AdvancedSettings.as_view(),
        name="manage_advanced",
    ),
]
