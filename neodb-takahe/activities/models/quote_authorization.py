import urlman
from core.snowflake import Snowflake
from django.db import models


class QuoteAuthorization(models.Model):
    """
    FEP-044f QuoteAuthorization granted for a quote of a local Post.

    Persisted so it can be dereferenced by third-party servers verifying the
    quote (e.g., Mastodon's ActivityPub::VerifyQuoteService). Without a real
    URL, the previous fragment-only identifier was unreachable over HTTP and
    quotes of NeoDB posts showed as unavailable on instances other than the
    quoter's own.
    """

    id = models.BigIntegerField(primary_key=True, default=Snowflake.generate_post)

    target_post = models.ForeignKey(
        "activities.Post",
        on_delete=models.CASCADE,
        related_name="quote_authorizations",
    )

    # The URI of the quoting Post (the one that asked for permission).
    interacting_object_uri = models.CharField(max_length=2048)

    # The id of the inbound QuoteRequest activity, echoed in Accept.object.
    request_uri = models.CharField(max_length=2048, blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["target_post", "interacting_object_uri"]),
        ]

    class urls(urlman.Urls):
        view = "{self.target_post.urls.view}quote-auth/{self.id}/"

        def get_scheme(self, url):
            return "https"

        def get_hostname(self, url):
            return self.instance.target_post.author.domain.uri_domain

    @property
    def object_uri(self) -> str:
        return f"{self.target_post.object_uri}quote-auth/{self.id}/"

    def to_ap(self) -> dict:
        return {
            "id": self.object_uri,
            "type": "QuoteAuthorization",
            "attributedTo": self.target_post.author.actor_uri,
            "interactingObject": self.interacting_object_uri,
            "interactionTarget": self.target_post.object_uri,
        }
