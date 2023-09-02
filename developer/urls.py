from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from oauth2_provider import views as oauth2_views
from oauth2_provider.views import oidc as oidc_views

from .views import *

_urlpatterns = [
    re_path(
        r"^oauth/authorize/$",
        oauth2_views.AuthorizationView.as_view(),
        name="authorize",
    ),
    re_path(r"^oauth/token/$", oauth2_views.TokenView.as_view(), name="token"),
    re_path(
        r"^oauth/revoke_token/$",
        oauth2_views.RevokeTokenView.as_view(),
        name="revoke-token",
    ),
    re_path(
        r"^oauth/introspect/$",
        oauth2_views.IntrospectTokenView.as_view(),
        name="introspect",
    ),
    re_path(
        r"^oauth/authorized_tokens/$",
        oauth2_views.AuthorizedTokensListView.as_view(),
        name="authorized-token-list",
    ),
    re_path(
        r"^oauth/authorized_tokens/(?P<pk>[\w-]+)/delete/$",
        oauth2_views.AuthorizedTokenDeleteView.as_view(),
        name="authorized-token-delete",
    ),
]

_urlpatterns += [
    path("developer/", console, name="developer"),
    re_path(
        r"^developer/applications/$",
        oauth2_views.ApplicationList.as_view(),
        name="list",
    ),
    re_path(
        r"^developer/applications/register/$",
        ApplicationRegistration.as_view(),
        name="register",
    ),
    re_path(
        r"^developer/applications/(?P<pk>[\w-]+)/$",
        oauth2_views.ApplicationDetail.as_view(),
        name="detail",
    ),
    re_path(
        r"^developer/applications/(?P<pk>[\w-]+)/delete/$",
        oauth2_views.ApplicationDelete.as_view(),
        name="delete",
    ),
    re_path(
        r"^developer/applications/(?P<pk>[\w-]+)/update/$",
        ApplicationUpdate.as_view(),
        name="update",
    ),
]

urlpatterns = [
    path("", include((_urlpatterns, "oauth2_provider"))),
]
