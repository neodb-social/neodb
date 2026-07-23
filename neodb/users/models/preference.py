from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from common.models.site_config import SiteConfig

from .user import User

RECO_KINDS = ("similar_items", "for_you", "from_circles")


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
    disabled_search_sources = models.JSONField(default=list)
    auto_bookmark_cats = models.JSONField(default=_default_book_cats)
    mastodon_append_tag = models.CharField(max_length=2048, default="")
    mastodon_default_repost = models.BooleanField(null=False, default=True)
    mastodon_repost_mode = models.PositiveSmallIntegerField(null=False, default=0)
    mastodon_skip_userinfo = models.BooleanField(null=False, default=False)
    mastodon_skip_relationship = models.BooleanField(null=False, default=False)
    mastodon_boost_enabled = models.BooleanField(null=True, default=False)
    # always write mark/review/article records to the linked Bluesky account's
    # PDS; the crosspost switch then only controls the timeline post
    bluesky_publish_records = models.BooleanField(null=False, default=False)
    disable_recommendations = models.BooleanField(null=True, default=False)
    # when replying to one's own catalog item post, turn the reply into a note
    auto_note_on_reply = models.BooleanField(null=False, default=True)

    def __str__(self):
        return str(self.user)

    def show_recommendations(self, kind: str) -> bool:
        """Whether to show the given recommendation surface to this user.

        Gate: user opt-in AND site master switch.
        ``kind`` is kept in the signature so callers can pass it through; the
        anonymous-vs-authenticated routing in ``catalog.recommendation`` still
        distinguishes which surfaces an anonymous viewer can see.
        """
        if self.disable_recommendations:
            return False
        return bool(SiteConfig.system.enable_recommendations)
