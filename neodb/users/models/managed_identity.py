from django.conf import settings
from django.db import models


class ManagedIdentityBinding(models.Model):
    """Bind one verified OneID subject to an existing NeoDB user.

    The pair ``(issuer, subject)`` is the only external identity key.  Source
    profile attributes and exchanged OAuth credentials deliberately do not
    belong on this Product-owned binding.
    """

    issuer = models.CharField(max_length=2048)
    subject = models.CharField(max_length=255)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_identity_bindings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issuer", "subject"],
                name="unique_managed_identity_issuer_subject",
            )
        ]

    def __str__(self) -> str:
        return f"{self.issuer}:{self.subject} -> {self.user_id}"
