"""An inbound Update older than the stored version does not rewrite the post."""

import pytest

from activities.models import Post
from users.models import Identity


def _capture_neodb_jobs(monkeypatch) -> list:
    enqueued: list = []
    monkeypatch.setattr(
        "django.conf.settings.NEODB_MQ",
        type("Queue", (), {"enqueue": lambda self, *a, **kw: enqueued.append(a)})(),
    )
    return enqueued


def _note(remote_identity: Identity, content: str, **dates) -> dict:
    return {
        "id": "https://remote.test/test-actor/posts/1",
        "type": "Note",
        "attributedTo": remote_identity.actor_uri,
        "content": f"<p>{content}</p>",
        "published": "2026-01-01T00:00:00Z",
        # makes takahe hand the post to NeoDB on each accepted version
        "relatedWith": [],
        **dates,
    }


@pytest.mark.django_db
def test_older_update_is_skipped(remote_identity: Identity, monkeypatch):
    enqueued = _capture_neodb_jobs(monkeypatch)
    Post.by_ap(
        _note(remote_identity, "new", updated="2026-01-03T00:00:00Z"), create=True
    )
    enqueued.clear()

    post = Post.by_ap(
        _note(remote_identity, "old", updated="2026-01-02T00:00:00Z"), update=True
    )

    post.refresh_from_db()
    assert "new" in post.content
    assert enqueued == []


@pytest.mark.django_db
def test_equal_and_newer_updates_apply(remote_identity: Identity, monkeypatch):
    enqueued = _capture_neodb_jobs(monkeypatch)
    Post.by_ap(
        _note(remote_identity, "first", updated="2026-01-02T00:00:00Z"), create=True
    )

    post = Post.by_ap(
        _note(remote_identity, "same", updated="2026-01-02T00:00:00Z"), update=True
    )
    post.refresh_from_db()
    assert "same" in post.content

    post = Post.by_ap(
        _note(remote_identity, "newer", updated="2026-01-04T00:00:00Z"), update=True
    )
    post.refresh_from_db()
    assert "newer" in post.content
    assert len(enqueued) == 3


@pytest.mark.django_db
def test_update_without_dates_compared_by_published(
    remote_identity: Identity, monkeypatch
):
    """An unedited post resent without `updated` (e.g. poll tallies) applies."""
    _capture_neodb_jobs(monkeypatch)
    Post.by_ap(_note(remote_identity, "first"), create=True)

    post = Post.by_ap(_note(remote_identity, "tally"), update=True)

    post.refresh_from_db()
    assert "tally" in post.content
