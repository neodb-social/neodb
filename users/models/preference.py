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

from common.utils import GenerateDateUUIDMediaFilePath
from management.models import Announcement
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
    default_no_share = models.BooleanField(default=False)
    default_visibility = models.PositiveSmallIntegerField(default=0)
    classic_homepage = models.PositiveSmallIntegerField(null=False, default=0)
    mastodon_publish_public = models.BooleanField(null=False, default=False)
    mastodon_append_tag = models.CharField(max_length=2048, default="")
    show_last_edit = models.PositiveSmallIntegerField(default=0)
    no_anonymous_view = models.PositiveSmallIntegerField(default=0)
    hidden_categories = models.JSONField(default=list)
    mastodon_skip_userinfo = models.BooleanField(null=False, default=False)
    mastodon_skip_relationship = models.BooleanField(null=False, default=False)

    def __str__(self):
        return str(self.user)
