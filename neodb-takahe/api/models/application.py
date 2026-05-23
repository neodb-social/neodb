import secrets

from django.db import models
from django.conf import settings


class Application(models.Model):
    """
    OAuth applications
    """

    client_id = models.CharField(max_length=500)
    client_secret = models.CharField(max_length=500)

    redirect_uris = models.TextField()
    scopes = models.TextField()

    name = models.CharField(max_length=500)
    website = models.CharField(max_length=500, blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @classmethod
    def create(
        cls,
        client_name: str,
        redirect_uris: str,
        website: str | None,
        scopes: str | None = None,
    ):
        client_id = "tk-" + secrets.token_urlsafe(16)
        client_secret = secrets.token_urlsafe(40)

        return cls.objects.create(
            name=client_name,
            website=website,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=redirect_uris,
            scopes=scopes or "read",
        )

    def to_mastodon_json(self, include_client_keys=True):
        return {
            "id": str(self.pk),
            "name": self.name,
            "website": self.website,
            "client_id": self.client_id if include_client_keys else "",
            "client_secret": self.client_secret if include_client_keys else "",
            "redirect_uris": self.redirect_uris,
            "vapid_key": settings.SETUP.VAPID_PUBLIC_KEY,
        }

    def to_mastodon_status_json(self):
        return {
            "name": self.name,
            "website": self.website,
        }
