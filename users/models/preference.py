from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils.translation import gettext_lazy as _

from .user import User


def _default_book_cats():
    return ["book"]


class Preference(models.Model):
    user = models.OneToOneField(User, models.CASCADE, primary_key=True)
    profile_layout = models.JSONField(
        blank=True,
        default=list,
    )
    discover_layout = models.JSONField(
        blank=True,
        default=list,
    )
    export_status = models.JSONField(
        blank=True, null=True, encoder=DjangoJSONEncoder, default=dict
    )  # deprecated
    import_status = models.JSONField(
        blank=True, null=True, encoder=DjangoJSONEncoder, default=dict
    )  # deprecated
    # 0: public, 1: follower only, 2: private
    default_visibility = models.PositiveSmallIntegerField(null=False, default=0)
    # 0: public, 1: unlisted, 4: local
    post_public_mode = models.PositiveSmallIntegerField(null=False, default=0)
    # 0: discover, 1: timeline, 2: my profile
    classic_homepage = models.PositiveSmallIntegerField(null=False, default=0)
    show_last_edit = models.PositiveSmallIntegerField(null=False, default=1)
    hidden_categories = models.JSONField(default=list)
    auto_bookmark_cats = models.JSONField(default=_default_book_cats)
    mastodon_append_tag = models.CharField(max_length=2048, default="")
    mastodon_default_repost = models.BooleanField(null=False, default=True)
    mastodon_repost_mode = models.PositiveSmallIntegerField(null=False, default=0)
    mastodon_skip_userinfo = models.BooleanField(null=False, default=False)
    mastodon_skip_relationship = models.BooleanField(null=False, default=False)

    def __str__(self):
        return str(self.user)
