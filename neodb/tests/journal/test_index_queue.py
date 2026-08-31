from unittest.mock import MagicMock, patch

from journal.search.index import (
    _PIECE_INDEX_RETRY_INTERVALS,
    JournalIndex,
    _update_journal_piece_index_task,
)


def test_piece_index_task_replaces_requested_pieces():
    index = MagicMock(spec=JournalIndex)
    with patch.object(JournalIndex, "instance", return_value=index):
        _update_journal_piece_index_task([41, 42])

    index.replace_pieces.assert_called_once_with([41, 42])


def test_piece_index_enqueue_uses_worker_retries():
    queue = MagicMock()
    retry = object()
    with (
        patch(
            "journal.search.index.django_rq.get_queue", return_value=queue
        ) as get_queue,
        patch("journal.search.index.Retry", return_value=retry) as retry_class,
    ):
        JournalIndex.enqueue_replace_pieces([41, 42])

    get_queue.assert_called_once_with("import")
    retry_class.assert_called_once_with(
        max=3,
        interval=_PIECE_INDEX_RETRY_INTERVALS,
    )
    queue.enqueue.assert_called_once_with(
        _update_journal_piece_index_task,
        [41, 42],
        retry=retry,
    )
