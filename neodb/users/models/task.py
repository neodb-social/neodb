import logging
import weakref
from typing import Self

import django_rq
from auditlog.context import set_actor
from django.db import models
from django.utils.translation import gettext_lazy as _
from typedmodels.models import TypedModel
from user_messages import api as msg

from users.middlewares import activate_language_for_user

from .task_files import delete_task_file, discard as _discard, is_stored, stage_locally
from .user import User

logger = logging.getLogger(__name__)


class Task(TypedModel):
    TaskQueue = "default"
    DefaultMetadata = {}

    class States(models.IntegerChoices):
        pending = 0, _("Pending")
        started = 1, _("Started")
        complete = 2, _("Complete")
        failed = 3, _("Failed")

    FileKeys = ("file", "matched_file")

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
        return f"{self.type.replace('.', '_')}-{self.pk}"

    def __str__(self):
        return self.job_id

    @classmethod
    def pending_tasks(cls, user: User):
        return cls.objects.filter(user=user, state__in=[0, 1])

    @classmethod
    def latest_task(cls, user: User):
        return cls.objects.filter(user=user).order_by("-created_time").first()

    @classmethod
    def create(cls, user: User, **kwargs) -> Self:
        d = cls.DefaultMetadata.copy()
        d.update(kwargs)
        t = cls.objects.create(user=user, metadata=d)
        return t

    #: Caches the staged copy behind ``local_file``. Kept off ``metadata`` on
    #: purpose, because several importers save mid-run and would otherwise
    #: persist a temp path that is gone by the next read.
    _local_file: str = ""

    @property
    def local_file(self) -> str:
        """A real filesystem path for ``metadata["file"]``.

        The recorded path is a storage key, which zipfile, openpyxl and lxml
        cannot open, so the object is staged to a temp file on first use and
        reused for the rest of the run. A path recorded before the files moved
        into storage is already local and is handed back untouched.

        Staging lazily rather than in ``_run()`` keeps a directly invoked
        ``run()`` working, which is how the importers are driven from tests
        and from the ``journal export`` command. The temp file is removed when
        this instance is collected.
        """
        if self._local_file:
            return self._local_file
        path = self.metadata.get("file") or ""
        if not path or not is_stored(path):
            return path
        self._local_file = stage_locally(path)
        weakref.finalize(self, _discard, self._local_file)
        return self._local_file

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

    def enqueue(self):
        return django_rq.get_queue(self.TaskQueue).enqueue(
            self._execute, self.pk, job_id=self.job_id
        )

    def cancel(self) -> None:
        """Drop this task's queued job, if it has not started yet."""
        try:
            job = django_rq.get_queue(self.TaskQueue).fetch_job(self.job_id)
            if job:
                job.cancel()
        except Exception as e:
            logger.warning(f"{self} cancel error {e}")

    def delete_files(self) -> bool:
        """Delete the file(s) this task owns, if any exist.

        Some importers (e.g. RYM) keep a derived ``matched_file`` alongside
        the original upload -- both belong to the task and must go together.

        Returns True if at least one file was deleted.
        """
        if not self.metadata:
            return False
        deleted = False
        for key in self.FileKeys:
            path = self.metadata.get(key)
            if path and delete_task_file(path):
                deleted = True
        return deleted

    def notify(self) -> None:
        ok = self.state == self.States.complete
        message = self.message or (None if ok else "Error occured.")
        if ok:
            msg.success(self.user, f"[{self.type}] {message}")
        else:
            msg.error(self.user, f"[{self.type}] {message}")

    def run(self) -> None:
        raise NotImplementedError("subclass must implement this")
