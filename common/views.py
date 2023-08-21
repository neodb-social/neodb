from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from users.models import User


@login_required
def me(request):
    return redirect(request.user.identity.url)


def home(request):
    if request.user.is_authenticated:
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
    usage = {"users": {"total": User.objects.count()}}
    # return estimated number of marks as posts, since count the whole table is slow
    # TODO filter local with SQL function in https://wiki.postgresql.org/wiki/Count_estimate
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT n_live_tup FROM pg_stat_all_tables WHERE relname = 'journal_shelfmember';"
        )
        row = cursor.fetchone()
        if row:
            usage["localPosts"] = row[0]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT n_live_tup FROM pg_stat_all_tables WHERE relname = 'journal_comment';"
        )
        row = cursor.fetchone()
        if row:
            usage["localComments"] = row[0]
    return JsonResponse(
        {
            "version": "2.0",
            "software": {
                "name": "neodb",
                "version": settings.NEODB_VERSION,
                "repository": "https://github.com/neodb-social/neodb",
                "homepage": "https://neodb.net/",
            },
            "protocols": ["activitypub", "neodb"],
            "openRegistrations": False,  # settings.SITE_INFO["open_registrations"],
            "services": {"outbound": [], "inbound": []},
            "usage": usage,
            "metadata": {"nodeName": settings.SITE_INFO["site_name"]},
        }
    )


def error_400(request, exception=None):
    return render(
        request,
        "400.html",
        {"exception": exception},
        status=400,
    )


def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request, exception=None):
    return render(request, "500.html", status=500)
