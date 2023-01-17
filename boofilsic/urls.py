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
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from users.views import login

urlpatterns = [
    path(settings.ADMIN_URL + "/", admin.site.urls),
    path("login/", login),
    path("markdownx/", include("markdownx.urls")),
    path("users/", include("users.urls")),
    path("", include("catalog.urls")),
    path("", include("journal.urls")),
    path("timeline/", include("social.urls")),
    path("announcement/", include("management.urls")),
    path("hijack/", include("hijack.urls")),
    path("", include("common.urls")),
    path("", include("legacy.urls")),
    path("tz_detect/", include("tz_detect.urls")),
]

urlpatterns += [path(settings.ADMIN_URL + "-rq/", include("django_rq.urls"))]

if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
