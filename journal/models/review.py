import re
from datetime import datetime
from functools import cached_property
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _
from markdownify import markdownify as md
from markdownx.models import MarkdownxField

from catalog.models import Item
from takahe.utils import Takahe
from users.models import APIdentity

from .common import Content
from .rating import Rating
from .renderers import render_md, render_post_with_macro, render_rating
from .shelf import ShelfManager

_RE_HTML_TAG = re.compile(r"<[^>]*>")
_RE_SPOILER_TAG = re.compile(r'<(div|span)\sclass="spoiler">.*</(div|span)>')


class Review(Content):
    url_path = "review"
    post_when_save = True
    index_when_save = True
    title = models.CharField(max_length=500, blank=False, null=False)
    body = MarkdownxField()

    @property
    def display_title(self):
        return self.title

    @property
    def brief_description(self):
        return self.plain_content[:155]

    @property
    def html_content(self):
        return render_md(self.body)

    @property
    def plain_content(self):
        html = render_md(self.body)
        return _RE_HTML_TAG.sub(
            " ", _RE_SPOILER_TAG.sub("***", html.replace("\n", " "))
        )

    @property
    def ap_object(self):
        return {
            "id": self.absolute_url,
            "type": "Review",
            "name": self.title,
            # "content": self.html_content,
            "content": self.body,
            "mediaType": "text/markdown",
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "withRegardTo": self.item.absolute_url,
            "href": self.absolute_url,
        }

    @classmethod
    def update_by_ap_object(cls, owner, item, obj, post, crosspost=None):
        p = cls.objects.filter(owner=owner, item=item).first()
        if p and p.edited_time >= datetime.fromisoformat(obj["updated"]):
            return p  # incoming ap object is older than what we have, no update needed
        content = (
            obj["content"]
            if obj.get("mediaType") == "text/markdown"
            else md(obj["content"])
        )
        d = {
            "title": obj["name"],
            "body": content,
            "local": False,
            "remote_id": obj["id"],
            "visibility": Takahe.visibility_t2n(post.visibility),
            "created_time": datetime.fromisoformat(obj["published"]),
            "edited_time": datetime.fromisoformat(obj["updated"]),
        }
        p, _ = cls.objects.update_or_create(owner=owner, item=item, defaults=d)
        p.link_post_id(post.id)
        return p

    def get_crosspost_postfix(self):
        tags = render_post_with_macro(
            self.owner.user.preference.mastodon_append_tag, self.item
        )
        return "\n" + tags if tags else ""

    def get_crosspost_template(self):
        return _(ShelfManager.get_action_template("reviewed", self.item.category))

    def to_crosspost_params(self):
        content = (
            self.get_crosspost_template().format(item=self.item.display_title)
            + " ##rating##\n##obj##\n##obj_link_if_plain##"
            + self.get_crosspost_postfix()
        )
        params = {"content": content, "obj": self, "rating": self.rating_grade}
        return params

    def to_post_params(self):
        item_link = f"{settings.SITE_INFO['site_url']}/~neodb~{self.item.url}"
        prepend_content = (
            self.get_crosspost_template().format(
                item=f'<a href="{item_link}">{self.item.display_title}</a>'
            )
            + f'<br><a href="{self.absolute_url}">{self.title}</a>'
        )
        content = f"{render_rating(self.rating_grade)}\n{self.get_crosspost_postfix()}"
        return {
            "prepend_content": prepend_content,
            "content": content,
        }

    @cached_property
    def mark(self):
        from .mark import Mark

        m = Mark(self.owner, self.item)
        m.review = self
        return m

    @cached_property
    def rating_grade(self):
        return Rating.get_item_rating(self.item, self.owner)

    @classmethod
    def update_item_review(
        cls,
        item: Item,
        owner: APIdentity,
        title: str | None,
        body: str | None,
        visibility=0,
        created_time=None,
        share_to_mastodon: bool = False,
    ):
        review = Review.objects.filter(owner=owner, item=item).first()
        if review is not None:
            if title is None:
                review.delete()
                return
        defaults = {
            "title": title,
            "body": body,
            "visibility": visibility,
        }
        if created_time:
            defaults["created_time"] = (
                created_time if created_time < timezone.now() else timezone.now()
            )
        if not review:
            review = Review(item=item, owner=owner, **defaults)
        else:
            for name, value in defaults.items():
                setattr(review, name, value)
        review.crosspost_when_save = share_to_mastodon
        review.save()
        return review

    def to_indexable_doc(self) -> dict[str, Any]:
        return {
            "item_id": [self.item.id],
            "item_class": [self.item.__class__.__name__],
            "item_title": self.item.to_indexable_titles(),
            "content": [self.title, self.body],
        }
