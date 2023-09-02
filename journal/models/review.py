import re
from functools import cached_property

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from markdownx.models import MarkdownxField

from catalog.models import Item
from mastodon.api import share_review
from users.models import User

from .common import Content
from .rating import Rating
from .renderers import render_md

_RE_HTML_TAG = re.compile(r"<[^>]*>")
_RE_SPOILER_TAG = re.compile(r'<(div|span)\sclass="spoiler">.*</(div|span)>')


class Review(Content):
    url_path = "review"
    title = models.CharField(max_length=500, blank=False, null=False)
    body = MarkdownxField()

    @property
    def html_content(self):
        return render_md(self.body)

    @property
    def plain_content(self):
        html = render_md(self.body)
        return _RE_HTML_TAG.sub(
            " ", _RE_SPOILER_TAG.sub("***", html.replace("\n", " "))
        )

    @cached_property
    def mark(self):
        from .mark import Mark

        m = Mark(self.owner, self.item)
        m.review = self
        return m

    @cached_property
    def rating_grade(self):
        return Rating.get_item_rating_by_user(self.item, self.owner)

    @classmethod
    def review_item_by_user(
        cls,
        item: Item,
        user: User,
        title: str | None,
        body: str | None,
        visibility=0,
        created_time=None,
        share_to_mastodon=False,
    ):
        if title is None:
            review = Review.objects.filter(owner=user, item=item).first()
            if review is not None:
                review.delete()
            return None
        defaults = {
            "title": title,
            "body": body,
            "visibility": visibility,
        }
        if created_time:
            defaults["created_time"] = (
                created_time if created_time < timezone.now() else timezone.now()
            )
        review, created = cls.objects.update_or_create(
            item=item, owner=user, defaults=defaults
        )
        if share_to_mastodon and user.mastodon_username:
            share_review(review)
        return review
