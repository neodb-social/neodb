from functools import cached_property

from django.db import models
from django.utils import timezone

from catalog.models import Item
from users.models import User

from .common import Content
from .rating import Rating
from .renderers import render_text


class Comment(Content):
    text = models.TextField(blank=False, null=False)

    @property
    def html(self):
        return render_text(self.text)

    @cached_property
    def rating_grade(self):
        return Rating.get_item_rating_by_user(self.item, self.owner)

    @cached_property
    def mark(self):
        from .mark import Mark

        m = Mark(self.owner, self.item)
        m.comment = self
        return m

    @property
    def item_url(self):
        if self.metadata.get("position"):
            return self.item.get_absolute_url_with_position(self.metadata["position"])
        else:
            return self.item.url

    @staticmethod
    def comment_item_by_user(
        item: Item, user: User, text: str | None, visibility=0, created_time=None
    ):
        comment = Comment.objects.filter(owner=user, item=item).first()
        if not text:
            if comment is not None:
                comment.delete()
                comment = None
        elif comment is None:
            comment = Comment.objects.create(
                owner=user,
                item=item,
                text=text,
                visibility=visibility,
                created_time=created_time or timezone.now(),
            )
        elif comment.text != text or comment.visibility != visibility:
            comment.text = text
            comment.visibility = visibility
            if created_time:
                comment.created_time = created_time
            comment.save()
        return comment
