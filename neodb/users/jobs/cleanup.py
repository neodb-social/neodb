import os
import shutil
from datetime import timedelta

from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager, SiteConfig
from journal.models import CrosspostRetry
from users.models import Task

_TASK_FILE_KEYS = ("file", "matched_file")


def _delete_path(file_path: str) -> bool:
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
            logger.debug(f"Deleted file {file_path}")
            # Remove parent directories if empty (date-based dirs like 2024/01/15/)
            parent = os.path.dirname(file_path)
            for _ in range(3):  # up to 3 levels (day/month/year)
                if parent and os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
                    logger.debug(f"Removed empty directory {parent}")
                    parent = os.path.dirname(parent)
                else:
                    break
            return True
        elif os.path.isdir(file_path):
            shutil.rmtree(file_path)
            logger.debug(f"Deleted directory {file_path}")
            return True
    except OSError as e:
        logger.warning(f"Failed to delete {file_path}: {e}")
    return False


def delete_task_file(task: Task) -> bool:
    """Delete the file(s) associated with a task, if any exist.

    Some importers (e.g. RYM) keep a derived ``matched_file`` alongside the
    original upload — both belong to the task and must be pruned together.

    Returns True if at least one file was deleted, False otherwise.
    """
    if not task.metadata:
        return False
    deleted = False
    for key in _TASK_FILE_KEYS:
        path = task.metadata.get(key)
        if path and _delete_path(path):
            deleted = True
    return deleted


def prune_tasks(days: int = 28) -> tuple[int, int]:
    """Delete tasks older than the given number of days and their files.

    Returns (tasks_deleted, files_deleted) counts.
    """
    if days <= 0:
        return 0, 0
    cutoff = timezone.now() - timedelta(days=days)
    old_tasks = Task.objects.filter(created_time__lt=cutoff)
    files_deleted = 0
    for task in old_tasks.iterator():
        if delete_task_file(task):
            files_deleted += 1
    tasks_deleted, _ = old_tasks.delete()
    return tasks_deleted, files_deleted


def prune_crosspost_retries(days: int = 28) -> int:
    """Delete crosspost retry records older than the given number of days."""
    if days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = CrosspostRetry.objects.filter(created_time__lt=cutoff).delete()
    return deleted


@JobManager.register
class TaskCleanup(BaseJob):
    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(days=1)

    def run(self) -> None:
        days = SiteConfig.system.task_cleanup_days
        if days <= 0:
            logger.info("Task cleanup skipped (task_cleanup_days <= 0).")
            return
        logger.info(f"Task cleanup job started (older than {days} days).")
        tasks_deleted, files_deleted = prune_tasks(days=days)
        retries_deleted = prune_crosspost_retries(days=days)
        logger.info(
            f"Task cleanup finished: {tasks_deleted} tasks deleted, {files_deleted} files deleted, "
            f"{retries_deleted} crosspost retry records deleted."
        )
