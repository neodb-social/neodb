from django.db import models


class BookLink(models.Model):
    old_id = models.IntegerField(unique=True)
    new_uid = models.UUIDField()


class MovieLink(models.Model):
    old_id = models.IntegerField(unique=True)
    new_uid = models.UUIDField()


class AlbumLink(models.Model):
    old_id = models.IntegerField(unique=True)
    new_uid = models.UUIDField()


class SongLink(models.Model):
    old_id = models.IntegerField(unique=True)
    new_uid = models.UUIDField()


class GameLink(models.Model):
    old_id = models.IntegerField(unique=True)
    new_uid = models.UUIDField()


class CollectionLink(models.Model):
    old_id = models.IntegerField(unique=True)
    new_uid = models.UUIDField()


class ReviewLink(models.Model):
    module = models.CharField(max_length=20)
    old_id = models.IntegerField()
    new_uid = models.UUIDField()

    class Meta:
        unique_together = [["module", "old_id"]]
