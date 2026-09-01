from django.conf import settings
from django.db import models


class ManagedCommunityProjection(models.Model):
    """Product-owned state for the one managed Community projection."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        PROVISIONED = "provisioned", "Provisioned"
        REJECTED = "rejected", "Rejected"
        UNKNOWN = "unknown", "Unknown"
        SUSPENDED = "suspended", "Suspended"
        SUSPEND_UNKNOWN = "suspend_unknown", "Suspend unknown"
        DELETING = "deleting", "Deleting"
        DELETE_UNKNOWN = "delete_unknown", "Delete unknown"
        DELETED = "deleted", "Deleted"

    class Operation(models.TextChoices):
        PROVISION = "provision", "Provision"
        READ = "read", "Read"
        SUSPEND = "suspend", "Suspend"
        RESUME = "resume", "Resume"
        DELETE = "delete", "Delete"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_community_projection",
    )
    binding = models.OneToOneField(
        "users.ManagedIdentityBinding",
        on_delete=models.PROTECT,
        related_name="managed_community_projection",
    )
    technical_handle = models.CharField(max_length=30, unique=True)
    technical_email = models.EmailField(max_length=255)
    display_seed = models.CharField(max_length=255, blank=True)
    state = models.CharField(
        max_length=24, choices=State.choices, default=State.PENDING
    )
    operation = models.CharField(
        max_length=16, choices=Operation.choices, default=Operation.PROVISION
    )
    remote_user_id = models.CharField(max_length=255, blank=True)
    remote_profile_url = models.URLField(max_length=2048, blank=True)
    managed_account = models.OneToOneField(
        "mastodon.SocialAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_community_projection",
    )
    last_error_category = models.CharField(max_length=40, blank=True)
    last_error_text = models.CharField(max_length=500, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "operation"], name="managed_comm_state_op"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.technical_handle}:{self.state}"
