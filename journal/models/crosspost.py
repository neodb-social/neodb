from django.db import models
from django.utils.translation import gettext_lazy as _


class CrosspostRetry(models.Model):
    """One row per (piece, platform) whose latest crosspost attempt failed.

    A successful (re)post deletes the row, so existence of a row means the
    crosspost is still missing on that platform.
    """

    class ErrorType(models.IntegerChoices):
        other = 0, _("Error")  # ty: ignore[invalid-assignment]
        auth = 1, _("Authorization expired")  # ty: ignore[invalid-assignment]

    class State(models.IntegerChoices):
        failed = 0, _("Failed")  # ty: ignore[invalid-assignment]
        retrying = 1, _("Retrying")  # ty: ignore[invalid-assignment]

    # transient, set by views for template rendering
    reauth_url: str | None = None

    piece_id: int  # set by the piece FK descriptor

    # denormalized from piece.owner.user for cheap owner-scoped queries
    user = models.ForeignKey("users.User", on_delete=models.CASCADE)
    piece = models.ForeignKey("journal.Piece", on_delete=models.CASCADE)
    # "mastodon" / "threads" / "bluesky", same literals as piece.metadata keys
    platform = models.CharField(max_length=20)
    error_type = models.IntegerField(
        choices=ErrorType.choices, default=ErrorType.other
    )
    message = models.TextField(default="")
    state = models.IntegerField(choices=State.choices, default=State.failed)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["piece", "platform"], name="unique_crosspost_retry"
            )
        ]
        indexes = [models.Index(fields=["user", "-created_time"])]

    def __str__(self):
        return f"CrosspostRetry:{self.pk}:{self.platform}:{self.piece_id}"

    @property
    def piece_url(self) -> str:
        """Link target for the piece: its own page when it has one,
        otherwise the item page it belongs to."""
        piece = self.piece
        if piece.url_path != "p":
            return piece.url
        item = getattr(piece, "item", None)
        return item.url if item else ""

    @property
    def piece_title(self) -> str:
        piece = self.piece
        item = getattr(piece, "item", None)
        if item:
            return item.display_title
        return getattr(piece, "display_title", "") or piece.classname
