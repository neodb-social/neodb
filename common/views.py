from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import DisallowedHost
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from boofilsic import __version__
from catalog.views import search as catalog_search
from journal.views import search as journal_search
from social.views import search as timeline_search
from takahe.utils import Takahe

from .api import api


def render_error(request: HttpRequest, title, message=""):
    return render(
        request, "common/error.html", {"msg": title, "secondary_msg": message}
    )


@login_required
def me(request):
    if not request.user.registration_complete:
        return redirect(reverse("users:register"))
    return redirect(request.user.identity.url)


def search(request):
    match request.GET.get("c", default="all").strip().lower():
        case "journal":
            return journal_search(request)
        case "timeline":
            return timeline_search(request)
        case _:
            return catalog_search(request)


def home(request):
    if request.user.is_authenticated:
        if not request.user.registration_complete:
            return redirect(reverse("users:register"))
        home = request.user.preference.classic_homepage
        if home == 1:
            return redirect(request.user.url)
        elif home == 2:
            return redirect(reverse("social:feed"))
        else:
            return redirect(reverse("catalog:discover"))
    else:
        return redirect(reverse("catalog:discover"))


def ap_redirect(request, uri):
    return redirect(request.get_full_path().replace("/~neodb~/", "/"))


def nodeinfo2(request):
    usage = cache.get("nodeinfo_usage", default={})
    return JsonResponse(
        {
            "version": "2.0",
            "software": {
                "name": "neodb",
                "version": __version__,
                "repository": "https://github.com/neodb-social/neodb",
                "homepage": "https://neodb.net/",
            },
            "protocols": ["activitypub", "neodb"],
            "openRegistrations": not settings.INVITE_ONLY,
            "services": {"outbound": [], "inbound": []},
            "usage": usage,
            "metadata": {
                "nodeName": settings.SITE_INFO["site_name"],
                "nodeRevision": settings.NEODB_VERSION,
                "nodeEnvironment": "development" if settings.DEBUG else "production",
            },
        }
    )


def _is_json_request(request) -> bool:
    return request.headers.get("HTTP_ACCEPT", "").endswith("json")


def error_400(request, exception=None):
    if isinstance(exception, DisallowedHost):
        url = settings.SITE_INFO["site_url"] + request.get_full_path()
        return redirect(url, permanent=True)
    if _is_json_request(request):
        return JsonResponse({"error": "invalid request"}, status=400)
    return render(request, "400.html", status=400, context={"exception": exception})


def error_403(request, exception=None):
    if _is_json_request(request):
        return JsonResponse({"error": "forbidden"}, status=403)
    return render(request, "403.html", status=403, context={"exception": exception})


def error_404(request, exception=None):
    if _is_json_request(request):
        return JsonResponse({"error": "not found"}, status=404)
    request.session.pop("next_url", None)
    return render(request, "404.html", status=404, context={"exception": exception})


def error_500(request, exception=None):
    if _is_json_request(request):
        return JsonResponse({"error": "something wrong"}, status=500)
    return render(request, "500.html", status=500, context={"exception": exception})


def console(request):
    token = None
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect(reverse("users:login"))
        app = Takahe.get_or_create_app(
            "Dev Console",
            settings.SITE_INFO["site_url"],
            "",
            owner_pk=0,
            client_id="app-00000000000-dev",
        )
        token = Takahe.refresh_token(app, request.user.identity.pk, request.user.pk)
    context = {
        "api": api,
        "token": token,
        "openapi_json_url": reverse(f"{api.urls_namespace}:openapi-json"),
    }
    return render(request, "console.html", context)


def signup(request, code: str | None = None):
    if request.user.is_authenticated:
        return redirect(reverse("common:home"))
    if code:
        return redirect(reverse("users:login") + "?invite=" + code)
    return redirect(reverse("users:login"))
