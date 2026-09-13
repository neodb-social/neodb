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

    @staticmethod
    def parse_redirect_uris(value: str) -> list[str]:
        """
        Split a registration into its callback URIs.

        Newlines are the Mastodon separator and what add_app writes. Older
        rows join with commas, which are legal inside a URI, so only treat a
        comma as a separator when there is no newline to separate on.
        """
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        separator = "\n" if "\n" in value else ","
        return [uri.strip() for uri in value.split(separator) if uri.strip()]

    @property
    def redirect_uri_list(self) -> list[str]:
        return self.parse_redirect_uris(self.redirect_uris)

    def matches_redirect_uri(self, uri: str) -> bool:
        """
        Whether uri is one of the registered callbacks, matched in full.

        A registration left empty keeps its historic allow-any behaviour for
        the apps that already rely on it; add_app rejects new ones.
        """
        if not uri:
            return False
        registered = self.redirect_uri_list
        if not registered:
            return True
        if uri in registered:
            return True
        # A lone registered URI holding a comma is split by the legacy
        # separator above, so accept the stored registration whole as well.
        stored = self.redirect_uris.strip()
        return "\n" not in stored and "\r" not in stored and uri == stored

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
