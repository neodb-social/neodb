from unittest.mock import MagicMock, patch

import pytest

from journal.search.index import (
    _PENDING_PIECE_INDEX_KEY,
    _PENDING_PIECE_INDEX_LOCK_KEY,
    JournalIndex,
    _schedule_journal_piece_index_task,
    _update_journal_piece_index_task,
)


def test_piece_index_schedule_uses_lock_not_reused_job_id():
    redis = MagicMock()
    redis.set.return_value = True
    queue = MagicMock()
    with (
        patch("journal.search.index.get_redis_connection", return_value=redis),
        patch("journal.search.index.django_rq.get_queue", return_value=queue),
    ):
        _schedule_journal_piece_index_task()

    redis.set.assert_called_once()
    queue.enqueue_in.assert_called_once()
    assert "job_id" not in queue.enqueue_in.call_args.kwargs


def test_piece_index_drain_reschedules_ids_added_while_finishing():
    redis = MagicMock()
    redis.spop.side_effect = [[b"41"], []]
    # Models an id added after the worker's final empty SPOP.
    redis.scard.return_value = 1
    index = MagicMock(spec=JournalIndex)
    with (
        patch("journal.search.index.get_redis_connection", return_value=redis),
        patch.object(JournalIndex, "instance", return_value=index),
        patch("journal.search.index._schedule_journal_piece_index_task") as schedule,
    ):
        _update_journal_piece_index_task()

    index.replace_pieces.assert_called_once_with([41])
    redis.delete.assert_called_once_with(_PENDING_PIECE_INDEX_LOCK_KEY)
    schedule.assert_called_once_with(2)


def test_piece_index_drain_restores_failed_chunk_before_retry():
    redis = MagicMock()
    redis.spop.return_value = [b"41", b"42"]
    redis.scard.return_value = 1
    index = MagicMock(spec=JournalIndex)
    index.replace_pieces.side_effect = RuntimeError("search unavailable")
    with (
        patch("journal.search.index.get_redis_connection", return_value=redis),
        patch.object(JournalIndex, "instance", return_value=index),
        patch("journal.search.index._schedule_journal_piece_index_task") as schedule,
        pytest.raises(RuntimeError, match="search unavailable"),
    ):
        _update_journal_piece_index_task()

    redis.sadd.assert_called_once_with(_PENDING_PIECE_INDEX_KEY, 41, 42)
    schedule.assert_called_once_with(30)
