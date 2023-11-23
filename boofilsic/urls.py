"""boofilsic URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from common.api import api
from users.views import login

urlpatterns = [
    path("api/", api.urls),  # type: ignore
    path("login/", login),
    path("markdownx/", include("markdownx.urls")),
    path("account/", include("users.urls")),
    path(
        "users/connect/",
        RedirectView.as_view(url="/account/connect", query_string=True),
    ),
    path(
        "users/OAuth2_login/",
        RedirectView.as_view(url="/account/login/oauth", query_string=True),
    ),
    path(
        "auth/edit",  # some apps like elk will use this url
        RedirectView.as_view(url="/account/profile", query_string=True),
    ),
    path("", include("catalog.urls")),
    path("", include("journal.urls")),
    path("timeline/", include("social.urls")),
    path("announcement/", include("management.urls")),
    path("hijack/", include("hijack.urls")),
    path("", include("common.urls")),
    path("", include("legacy.urls")),
    path("", include("developer.urls")),
    path("", include("takahe.urls")),
    # path("oauth/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    path("tz_detect/", include("tz_detect.urls")),
    path(settings.ADMIN_URL + "/", admin.site.urls),
    path(settings.ADMIN_URL + "-rq/", include("django_rq.urls")),
]

if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler400 = "common.views.error_400"
handler403 = "common.views.error_403"
handler404 = "common.views.error_404"
handler500 = "common.views.error_500"
