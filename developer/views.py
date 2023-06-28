from django.shortcuts import render
from loguru import logger
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from oauth2_provider.forms import AllowForm
from oauth2_provider.models import get_application_model
from oauth2_provider.views import ProtectedResourceView
from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView
from oauth2_provider.settings import oauth2_settings
from common.api import api
from oauthlib.common import generate_token
from oauth2_provider.models import AccessToken
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from oauth2_provider.models import RefreshToken
from django.conf import settings
from .models import Application


class AuthorizationView(BaseAuthorizationView):
    pass


@login_required
def console(request):
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
    return render(request, "console.html", context)
