import logging
from datetime import timedelta

from django.utils import timezone

from common.models import BaseJob, JobManager, SiteConfig
from journal.models import CrosspostRetry
from takahe.models import Identity
from takahe.utils import Takahe
from users.models import APIdentity, Task

logger = logging.getLogger(__name__)


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
        if task.delete_files():
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


def clear_identity_data(identity_pk: int) -> None:
    """Remove an identity's data, whatever route asked for the deletion.

    The user goes first and the identity second, the same order and the same
    pair of steps as ``takahe.ap_handlers.identity_deleted``. Both are needed
    here, because a deletion started on the Takahe side reaches us only
    through that callback: if it is lost, this is what runs instead, and
    clearing the identity alone would leave the account live with its social
    accounts, tasks, export files, webhooks and preferences. Both calls are
    safe to repeat.
    """
    identity = APIdentity.objects.filter(pk=identity_pk).first()
    if not identity:
        logger.error(f"identity {identity_pk} not found, unable to clear data")
        return
    if identity.user:
        identity.user.clear()
    identity.clear()


def reconcile_deleted_users() -> tuple[int, int]:
    """Finish deletions that did not run all the way through.

    ``initiate_user_deletion`` clears the user, then queues the identity
    clear and asks Takahe to delete the identity. Either leg can be lost: the
    queue job can vanish, and the Takahe handler swallows its own exceptions
    and consumes the message. The first leaves every journal row, file and
    index entry in place; the second leaves posts, follows and tokens live on
    the Takahe side.

    Each leg is detected by the stamp the other one left, and both stamps are
    written by deletion code alone: ``APIdentity.deleted`` by
    ``APIdentity.clear()`` and ``Identity.deleted`` by Takahe's
    ``mark_deleted()``. Nothing infers a deletion from the user row, because
    no field there is reserved for it -- ``is_active`` also means suspended,
    and the name fields are editable in the admin.

    Returns (identities cleared, identity deletions re-requested).
    """
    # Takahe deleted the identity, but our data removal never ran.
    takahe_deleted = list(
        Identity.objects.filter(local=True, deleted__isnull=False).values_list(
            "pk", flat=True
        )
    )
    cleared = 0
    for identity in APIdentity.objects.filter(
        pk__in=takahe_deleted, deleted__isnull=True
    ):
        logger.warning(f"Identity {identity} data removal incomplete, clearing now")
        try:
            clear_identity_data(identity.pk)
            cleared += 1
        except Exception as e:
            logger.error(f"Identity {identity} clear error {e}")
    # Our data is gone, but Takahe still holds the identity. Re-requesting is
    # safe, because Takahe ignores the message for one already deleted.
    ours_deleted = list(
        APIdentity.objects.filter(local=True, deleted__isnull=False).values_list(
            "pk", flat=True
        )
    )
    requested = 0
    for pk in Identity.objects.filter(
        pk__in=ours_deleted, local=True, deleted__isnull=True
    ).values_list("pk", flat=True):
        logger.warning(f"Identity {pk} still live in Takahe, requesting deletion")
        if Takahe.request_delete_identity(pk):
            requested += 1
    return cleared, requested


@JobManager.register
class DeletedUserCleanup(BaseJob):
    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(hours=1)

    def run(self) -> None:
        cleared, requested = reconcile_deleted_users()
        if cleared or requested:
            logger.warning(
                f"Deleted user cleanup finished: {cleared} identities cleared, "
                f"{requested} identity deletions re-requested"
            )
