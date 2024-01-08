import hashlib
import re
from functools import cached_property
from operator import index

from auditlog.context import set_actor
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

from management.models import Announcement
from mastodon.api import *
from takahe.utils import Takahe

from .user import User


class Task(models.Model):
    TaskQueue = "default"
    TaskType = "unknown"
    DefaultMetadata = {}

    class States(models.IntegerChoices):
        pending = 0, "Pending"
        started = 1, "Started"
        complete = 2, "Complete"
        failed = 3, "Failed"

    user = models.ForeignKey(User, models.CASCADE, null=False)
    type = models.CharField(max_length=20, null=False)
    state = models.IntegerField(choices=States.choices, default=States.pending)
    metadata = models.JSONField(null=False, default=dict)
    message = models.TextField(default="")
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user", "type"])]

    @property
    def job_id(self):
        if not self.pk:
            raise ValueError("task not saved yet")
        return f"{self.type}-{self.user}-{self.pk}"

    def __str__(self):
        return self.job_id

    @classmethod
    def enqueue(cls, user: User, **kwargs) -> "Task":
        d = cls.DefaultMetadata.copy()
        d.update(kwargs)
        t = cls.objects.create(user=user, type=cls.TaskType, metadata=d)
        django_rq.get_queue(cls.TaskQueue).enqueue(cls._run, t.pk, job_id=t.job_id)
        return t

    @classmethod
    def _run(cls, task_id: int):
        task = cls.objects.get(pk=task_id)
        try:
            task.state = cls.States.started
            task.save(update_fields=["state"])
            with set_actor(task.user):
                task.run()
            task.state = cls.States.complete
            task.save(update_fields=["state"])
        except Exception as e:
            logger.error(f"Task {task} Exception {e}")
            task.message = "Error occured."
            task.state = cls.States.failed
            task.save(update_fields=["state", "message"])

    def run(self) -> None:
        raise NotImplemented
