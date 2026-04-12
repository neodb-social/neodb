import os
import shutil
from datetime import timedelta

from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager, SiteConfig
from users.models import Task


def delete_task_file(task: Task) -> bool:
    """Delete the file associated with a task, if it exists.

    Returns True if a file was deleted, False otherwise.
    """
    file_path = task.metadata.get("file") if task.metadata else None
    if not file_path:
        return False
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
        logger.info(
            f"Task cleanup finished: {tasks_deleted} tasks deleted, {files_deleted} files deleted."
        )
