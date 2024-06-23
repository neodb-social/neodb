from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class MastodonApplication(models.Model):
    domain_name = models.CharField(_("site domain name"), max_length=200, unique=True)
    api_domain = models.CharField(_("domain for api call"), max_length=200, blank=True)
    server_version = models.CharField(_("type and verion"), max_length=200, blank=True)
    app_id = models.CharField(_("in-site app id"), max_length=200)
    client_id = models.CharField(_("client id"), max_length=200)
    client_secret = models.CharField(_("client secret"), max_length=200)
    vapid_key = models.CharField(_("vapid key"), max_length=200, null=True, blank=True)
    star_mode = models.PositiveIntegerField(
        _("0: custom emoji; 1: unicode moon; 2: text"), blank=False, default=0
    )
    max_status_len = models.PositiveIntegerField(
        _("max toot len"), blank=False, default=500
    )
    last_reachable_date = models.DateTimeField(null=True, default=None)
    disabled = models.BooleanField(default=False)
    is_proxy = models.BooleanField(default=False, blank=True)
    proxy_to = models.CharField(max_length=100, blank=True, default="")

    def __str__(self):
        return self.domain_name
