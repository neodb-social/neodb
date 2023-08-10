from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.forms.models import modelform_factory
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from loguru import logger
from oauth2_provider.forms import AllowForm
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import AccessToken, RefreshToken, get_application_model
from oauth2_provider.settings import oauth2_settings
from oauth2_provider.views import ApplicationRegistration as BaseApplicationRegistration
from oauth2_provider.views import ApplicationUpdate as BaseApplicationUpdate
from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView
from oauthlib.common import generate_token

from common.api import api

from .models import Application


class ApplicationRegistration(BaseApplicationRegistration):
    def get_form_class(self):
        return modelform_factory(
            Application,
            fields=(
                "name",
                "url",
                "description",
                "client_secret",
                "redirect_uris",
                # "post_logout_redirect_uris",
            ),
        )

    def form_valid(self, form):
        form.instance.user = self.request.user
        if not form.instance.id:
            form.instance.client_id = generate_client_id()
            # form.instance.client_secret = generate_client_secret()
            form.instance.client_type = Application.CLIENT_CONFIDENTIAL
            form.instance.authorization_grant_type = (
                Application.GRANT_AUTHORIZATION_CODE
            )
        return super().form_valid(form)


class ApplicationUpdate(BaseApplicationUpdate):
    def get_form_class(self):
        return modelform_factory(
            get_application_model(),
            fields=(
                "name",
                "url",
                "description",
                # "client_secret",
                "redirect_uris",
                # "post_logout_redirect_uris",
            ),
        )


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
