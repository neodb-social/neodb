import hashlib

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class WebAuthnCredential(models.Model):
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="webauthn_credentials",
    )
    name = models.CharField(_("name"), max_length=255, default="Passkey")
    credential_id = models.BinaryField(_("credential ID"), unique=True)
    public_key = models.BinaryField(_("public key"))
    sign_count = models.PositiveIntegerField(_("sign count"), default=0)
    transports = models.JSONField(_("transports"), default=list)
    created = models.DateTimeField(_("created"), auto_now_add=True)
    last_used = models.DateTimeField(_("last used"), null=True, default=None)

    class Meta:
        indexes = [
            models.Index(fields=["user"], name="index_webauthn_user"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.user})"

    @staticmethod
    def get_webauthn_user_id(user) -> bytes:
        """Derive a stable, opaque WebAuthn user handle from user PK."""
        return hashlib.sha256(
            f"{settings.SECRET_KEY}:webauthn:{user.pk}".encode()
        ).digest()
