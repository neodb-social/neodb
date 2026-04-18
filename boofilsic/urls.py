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

from functools import wraps

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.urls import URLPattern, URLResolver, include, path
from django.views.generic import RedirectView

from common.api import api
from users.views import login


def _superuser_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_superuser):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def _gate_urlpatterns(patterns, decorator):
    for p in patterns:
        if isinstance(p, URLPattern):
            p.callback = decorator(p.callback)
        elif isinstance(p, URLResolver):
            _gate_urlpatterns(p.url_patterns, decorator)


# Restrict django-rq to superusers:
#   * the standalone `/admin-rq/` include (below) has every view wrapped
#   * the Django-admin Dashboard entry (auto-registered when
#     RQ_SHOW_ADMIN_LINK is True) is re-registered with a superuser-only
#     QueueAdmin so it shows up for supers but 403s everyone else
import django_rq.admin as _django_rq_admin  # noqa: E402
import django_rq.urls as _django_rq_urls  # noqa: E402
from django_rq.models import Dashboard as _RQDashboard  # noqa: E402

_gate_urlpatterns(_django_rq_urls.urlpatterns, _superuser_required)


class _SuperuserQueueAdmin(_django_rq_admin.QueueAdmin):
    def has_module_permission(self, request):
        return bool(
            getattr(request, "user", None)
            and request.user.is_active
            and request.user.is_superuser
        )

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def get_urls(self):
        patterns = super().get_urls()
        _gate_urlpatterns(patterns, _superuser_required)
        return patterns


from django.contrib.admin.exceptions import NotRegistered  # noqa: E402

try:
    admin.site.unregister(_RQDashboard)
except NotRegistered:
    pass

admin.site.register(_RQDashboard, _SuperuserQueueAdmin)

urlpatterns = [
    path("api/", api.urls),
    path("login/", login),
    path("captcha/", include("captcha.urls")),
    path("account/", include("users.urls")),
    path("account/", include("mastodon.urls")),
    path(
        "users/connect/",
        RedirectView.as_view(url="/mastodon/login", query_string=True),
    ),
    path(
        "auth/edit",  # some apps like elk will use this url
        RedirectView.as_view(url="/account/profile", query_string=True),
    ),
    path("", include("catalog.urls")),
    path("", include("journal.urls")),
    path("timeline/", include("social.urls")),
    path("hijack/", include("hijack.urls")),
    path("", include("common.urls")),
    path("", include("legacy.urls")),
    path("", include("takahe.urls")),
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
