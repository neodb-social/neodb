import hashlib
import re
from functools import cached_property

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core import validators
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.functions import Concat, Lower
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _
from loguru import logger

from mastodon.api import *
from takahe.utils import Takahe

from .user import User


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
    )
    import_status = models.JSONField(
        blank=True, null=True, encoder=DjangoJSONEncoder, default=dict
    )
    # 0: public, 1: follower only, 2: private
    default_visibility = models.PositiveSmallIntegerField(null=False, default=0)
    # 0: public, 1: unlisted, 4: local
    post_public_mode = models.PositiveSmallIntegerField(null=False, default=0)
    # 0: discover, 1: timeline, 2: my profile
    classic_homepage = models.PositiveSmallIntegerField(null=False, default=0)
    show_last_edit = models.PositiveSmallIntegerField(null=False, default=1)
    no_anonymous_view = models.PositiveSmallIntegerField(default=0)  # TODO remove
    hidden_categories = models.JSONField(default=list)
    mastodon_append_tag = models.CharField(max_length=2048, default="")
    mastodon_default_repost = models.BooleanField(null=False, default=True)
    mastodon_repost_mode = models.PositiveSmallIntegerField(null=False, default=0)
    mastodon_skip_userinfo = models.BooleanField(null=False, default=False)
    mastodon_skip_relationship = models.BooleanField(null=False, default=False)
    # Removed:
    # mastodon_publish_public = models.BooleanField(null=False, default=False)
    # default_no_share = models.BooleanField(null=False, default=False)

    def __str__(self):
        return str(self.user)
