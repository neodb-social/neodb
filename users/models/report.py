from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger

from common.utils import GenerateDateUUIDMediaFilePath
from mastodon.api import *

from .user import User


def report_image_path(instance, filename):
    return GenerateDateUUIDMediaFilePath(
        instance, filename, settings.REPORT_MEDIA_PATH_ROOT
    )


class Report(models.Model):
    submit_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="sumbitted_reports", null=True
    )
    reported_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="accused_reports", null=True
    )
    image = models.ImageField(
        upload_to=report_image_path,
        blank=True,
        default="",
    )
    is_read = models.BooleanField(default=False)
    submitted_time = models.DateTimeField(auto_now_add=True)
    message = models.CharField(max_length=1000)
