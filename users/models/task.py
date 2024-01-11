import django_rq
from auditlog.context import set_actor
from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger
from user_messages import api as msg

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
    def latest_task(cls, user: User):
        return (
            cls.objects.filter(user=user, type=cls.TaskType)
            .order_by("-created_time")
            .first()
        )

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
        task = cls.objects.get(pk=task_id)
        if task.message:
            if task.state == cls.States.complete:
                msg.success(task.user, f"[{task.type}] {task.message}")
            else:
                msg.error(task.user, f"[{task.type}] {task.message}")

    def run(self) -> None:
        raise NotImplemented
