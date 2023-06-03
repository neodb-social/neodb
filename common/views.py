from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from .api import api
from oauthlib.common import generate_token
from oauth2_provider.models import AccessToken, Application
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from oauth2_provider.models import RefreshToken
from django.conf import settings


@login_required
def me(request):
    return redirect(
        reverse("journal:user_profile", args=[request.user.mastodon_username])
    )


def home(request):
    if request.user.is_authenticated:
        home = request.user.get_preference().classic_homepage
        if home == 1:
            return redirect(
                reverse("journal:user_profile", args=[request.user.mastodon_username])
            )
        elif home == 2:
            return redirect(reverse("social:feed"))
        else:
            return redirect(reverse("catalog:discover"))
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


@login_required
def developer(request):
    token = None
    if request.method == "POST":
        user = request.user
        app = Application.objects.filter(
            client_id=settings.DEVELOPER_CONSOLE_APPLICATION_CLIENT_ID
        ).first()
        if app:
            for token in AccessToken.objects.filter(user=user, application=app):
                token.revoke()
            token = generate_token()
            AccessToken.objects.create(
                user=user,
                application=app,
                scope="read write",
                expires=timezone.now() + relativedelta(days=365),
                token=token,
            )
        else:
            token = "Configuration error, contact admin"
    context = {
        "api": api,
        "token": token,
        "openapi_json_url": reverse(f"{api.urls_namespace}:openapi-json"),
    }
    return render(request, "developer.html", context)
