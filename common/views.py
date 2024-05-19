from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from boofilsic import __version__
from users.models import User


@login_required
def me(request):
    if not request.user.registration_complete:
        return redirect(reverse("users:register"))
    return redirect(request.user.identity.url)


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
    return redirect(uri)


def nodeinfo2(request):
    usage = cache.get("nodeinfo_usage") or {}
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
            },
        }
    )


def error_400(request, exception=None):
    return render(request, "400.html", status=400, context={"exception": exception})


def error_403(request, exception=None):
    return render(request, "403.html", status=403, context={"exception": exception})


def error_404(request, exception=None):
    return render(request, "404.html", status=404, context={"exception": exception})


def error_500(request, exception=None):
    return render(request, "500.html", status=500, context={"exception": exception})
