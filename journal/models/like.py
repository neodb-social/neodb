from django.contrib.contenttypes.models import ContentType
from django.db import connection, models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from users.models import APIdentity

from .common import Piece


class Like(Piece):  # TODO remove
    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Public / 1: Follower only / 2: Self only
    created_time = models.DateTimeField(default=timezone.now)
    edited_time = models.DateTimeField(default=timezone.now)
    target = models.ForeignKey(Piece, on_delete=models.CASCADE, related_name="likes")

    @staticmethod
    def user_liked_piece(owner, piece):
        return Like.objects.filter(owner=owner, target=piece).exists()

    @staticmethod
    def user_like_piece(owner, piece):
        if not piece:
            return
        like = Like.objects.filter(owner=owner, target=piece).first()
        if not like:
            like = Like.objects.create(owner=owner, target=piece)
        return like

    @staticmethod
    def user_unlike_piece(owner, piece):
        if not piece:
            return
        Like.objects.filter(owner=owner, target=piece).delete()

    @staticmethod
    def user_likes_by_class(owner, cls):
        ctype_id = ContentType.objects.get_for_model(cls)
        return Like.objects.filter(owner=owner, target__polymorphic_ctype=ctype_id)
