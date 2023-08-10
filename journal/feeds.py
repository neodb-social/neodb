import mimetypes

from django.conf import settings
from django.contrib.syndication.views import Feed

from journal.models.renderers import render_md

from .models import *

MAX_ITEM_PER_TYPE = 10


class ReviewFeed(Feed):
    def get_object(self, request, id):
        return User.get(id)

    def title(self, user):
        return "%s的评论" % user.display_name if user else "无效链接"

    def link(self, user):
        return user.url if user else settings.SITE_INFO["site_url"]

    def description(self, user):
        return "%s的评论合集 - NeoDB" % user.display_name if user else "无效链接"

    def items(self, user):
        if user is None or user.preference.no_anonymous_view:
            return []
        reviews = Review.objects.filter(owner=user, visibility=0)[:MAX_ITEM_PER_TYPE]
        return reviews

    def item_title(self, item: Review):
        return f"{item.title} - 评论《{item.item.title}》"

    def item_description(self, item: Review):
        target_html = (
            f'<p><a href="{item.item.absolute_url}">{item.item.title}</a></p>\n'
        )
        html = render_md(item.body)
        return target_html + html

    # item_link is only needed if NewsItem has no get_absolute_url method.
    def item_link(self, item: Review):
        return item.absolute_url

    def item_categories(self, item):
        return [item.item.category.label]

    def item_pubdate(self, item):
        return item.created_time

    def item_updateddate(self, item):
        return item.edited_time

    def item_enclosure_url(self, item):
        return item.item.cover.url

    def item_enclosure_mime_type(self, item):
        t, _ = mimetypes.guess_type(item.item.cover.url)
        return t

    def item_enclosure_length(self, item):
        try:
            size = item.item.cover.file.size
        except Exception:
            size = None
        return size

    def item_comments(self, item):
        return item.absolute_url
