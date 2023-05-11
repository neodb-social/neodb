from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from .api import api


@login_required
def me(request):
    return redirect(
        reverse("journal:user_profile", args=[request.user.mastodon_username])
    )


@login_required
def home(request):
    home = request.user.get_preference().classic_homepage
    if home == 1:
        return redirect(
            reverse("journal:user_profile", args=[request.user.mastodon_username])
        )
    elif home == 2:
        return redirect(reverse("social:feed"))
    else:
        return redirect(reverse("catalog:discover"))


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


def api_doc(request):
    context = {
        "api": api,
        "openapi_json_url": reverse(f"{api.urls_namespace}:openapi-json"),
    }
    return render(request, "api_doc.html", context)
