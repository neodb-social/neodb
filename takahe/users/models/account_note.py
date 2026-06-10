from django.db import models


class AccountNote(models.Model):
    """
    A private note that one identity has written about another.
    Independent of follow/block/mute relationships.
    """

    source = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="account_notes_by",
    )
    target = models.ForeignKey(
        "users.Identity",
        on_delete=models.CASCADE,
        related_name="account_notes_about",
    )
    note = models.TextField(blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("source", "target")]

    def __str__(self) -> str:
        return f"#{self.pk}: {self.source} note on {self.target}"
