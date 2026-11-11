from django.db import models


class PostTombstone(models.Model):
    """
    The object URI of a remote post its author deleted, so that replaying the
    post's signed Create cannot bring it back.
    """

    object_uri = models.CharField(max_length=2048, unique=True)
    created = models.DateTimeField(auto_now_add=True)
