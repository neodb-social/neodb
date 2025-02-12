from typing import Self

import django_rq
from auditlog.context import set_actor
from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger
from typedmodels.models import TypedModel
from user_messages import api as msg

from users.middlewares import activate_language_for_user

from .user import User


class Task(TypedModel):
    TaskQueue = "default"
    DefaultMetadata = {}

    class States(models.IntegerChoices):
        pending = 0, _("Pending")  # type:ignore[reportCallIssue]
        started = 1, _("Started")  # type:ignore[reportCallIssue]
        complete = 2, _("Complete")  # type:ignore[reportCallIssue]
        failed = 3, _("Failed")  # type:ignore[reportCallIssue]

    user = models.ForeignKey(User, models.CASCADE, null=False)
    # type = models.CharField(max_length=20, null=False)
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
        return f"{self.type}-{self.pk}"

    def __str__(self):
        return self.job_id

    @classmethod
    def latest_task(cls, user: User):
        return cls.objects.filter(user=user).order_by("-created_time").first()

    @classmethod
    def create(cls, user: User, **kwargs) -> Self:
        d = cls.DefaultMetadata.copy()
        d.update(kwargs)
        t = cls.objects.create(user=user, metadata=d)
        return t

    def _run(self) -> bool:
        activate_language_for_user(self.user)
        with set_actor(self.user):
            try:
                self.run()
                return True
            except Exception as e:
                logger.exception(
                    f"error running {self.__class__}",
                    extra={"exception": e, "task": self.pk},
                )
                return False

    @classmethod
    def _execute(cls, task_id: int):
        task = cls.objects.get(pk=task_id)
        logger.info(f"running {task}")
        if task.state != cls.States.pending:
            logger.warning(
                f"task {task_id} is not pending, skipping", extra={"task": task_id}
            )
            return
        task.state = cls.States.started
        task.save()
        ok = task._run()
        task.refresh_from_db()
        task.state = cls.States.complete if ok else cls.States.failed
        task.save()
        task.notify()

    def enqueue(self):
        return django_rq.get_queue(self.TaskQueue).enqueue(
            self._execute, self.pk, job_id=self.job_id
        )

    def notify(self) -> None:
        ok = self.state == self.States.complete
        message = self.message or (None if ok else "Error occured.")
        if ok:
            msg.success(self.user, f"[{self.type}] {message}")
        else:
            msg.error(self.user, f"[{self.type}] {message}")

    def run(self) -> None:
        raise NotImplementedError("subclass must implement this")
