import urlman
from core.snowflake import Snowflake
from django.db import models


class FeatureAuthorization(models.Model):
    """
    FEP-7aa9 FeatureAuthorization granted for including a local Identity in a
    remote FeaturedCollection (Mastodon calls these "collections").

    Persisted so it can be dereferenced by third-party servers verifying the
    membership (e.g., Mastodon's ActivityPub::VerifyFeaturedItemService), which
    requires the stamp to be resolvable and hosted on the same domain as the
    featured actor.
    """

    id = models.BigIntegerField(primary_key=True, default=Snowflake.generate_identity)

    identity = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="feature_authorizations",
    )

    # The URI of the FeaturedCollection that asked for permission.
    collection_uri = models.CharField(max_length=2048)

    # The id of the inbound FeatureRequest activity, echoed in Accept.object.
    request_uri = models.CharField(max_length=2048, blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["identity", "collection_uri"]),
        ]

    class urls(urlman.Urls):
        view = "{self.identity.urls.view}feature-auth/{self.id}/"

        def get_scheme(self, url):
            return "https"

        def get_hostname(self, url):
            return self.instance.identity.domain.uri_domain

    @property
    def object_uri(self) -> str:
        return f"{self.identity.actor_uri}feature-auth/{self.id}/"

    def to_ap(self) -> dict:
        return {
            "id": self.object_uri,
            "type": "FeatureAuthorization",
            "interactingObject": self.collection_uri,
            "interactionTarget": self.identity.actor_uri,
        }
