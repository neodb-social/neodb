from django.shortcuts import redirect, render
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from .api import api


@login_required
def home(request):
    if request.user.get_preference().classic_homepage:
        return redirect(
            reverse("journal:user_profile", args=[request.user.mastodon_username])
        )
    else:
        return redirect(reverse("catalog:discover"))


def error_400(request, exception=None):
    return render(request, "400.html", status=400)


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
