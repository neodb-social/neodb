"""Remote pieces keep the remote version time, so edits are ordered by it.

edited_time is auto_now; it used to hold the local save time, and an edit
made before that moment was dropped as stale.
"""

from datetime import datetime
from typing import Any, Callable

import pytest
from django.utils import timezone

from catalog.models import Edition
from journal.models import Comment, Note, Rating, Review, ShelfMember
from takahe.models import Domain, Post
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-01-02T00:00:00+00:00"
T2 = "2026-01-03T00:00:00+00:00"


def _rating(n: int, value: int, updated: str) -> dict[str, Any]:
    return {
        "id": f"https://remote.example/rating/{n}",
        "type": "Rating",
        "best": 10,
        "worst": 1,
        "value": value,
        "published": T0,
        "updated": updated,
    }


def _review(n: int, text: str, updated: str) -> dict[str, Any]:
    return {
        "id": f"https://remote.example/review/{n}",
        "type": "Review",
        "name": text,
        "content": text,
        "mediaType": "text/markdown",
        "published": T0,
        "updated": updated,
    }


def _comment(n: int, text: str, updated: str) -> dict[str, Any]:
    return {
        "id": f"https://remote.example/comment/{n}",
        "type": "Comment",
        "content": text,
        "published": T0,
        "updated": updated,
    }


def _status(n: int, status: str, updated: str) -> dict[str, Any]:
    return {
        "id": f"https://remote.example/status/{n}",
        "type": "Status",
        "status": status,
        "published": T0,
        "updated": updated,
    }


# model, object builder, two versions, how to read the version back
CASES: list[tuple[Any, Callable[..., dict], Any, Any, Callable[[Any], Any]]] = [
    (Rating, _rating, 6, 8, lambda p: p.grade),
    (Review, _review, "first", "second", lambda p: p.title),
    (Comment, _comment, "first", "second", lambda p: p.text),
    (ShelfMember, _status, "wishlist", "complete", lambda p: p.shelf_type),
]


@pytest.mark.django_db(databases="__all__")
class TestRemoteEditOrder:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        domain, _ = Domain.objects.get_or_create(
            domain="remote.example", defaults={"local": False}
        )
        identity = TakaheIdentity.objects.create(
            actor_uri="https://remote.example/users/reader/",
            local=False,
            username="reader",
            domain=domain,
        )
        self.owner = Takahe.get_or_create_remote_apidentity(identity)
        self.book = Edition.objects.create(title="A Book")

    def _post(self, obj: dict) -> Post:
        return Post.objects.create(
            author_id=self.owner.pk,
            local=False,
            object_uri=obj["id"],
            content="",
            type="Note",
            type_data={"object": {"relatedWith": [obj]}},
            visibility=Post.Visibilities.public,
            state="fanned_out",
        )

    def _apply(self, cls, obj: dict, post: Post | None = None):
        return cls.update_by_ap_object(
            self.owner, self.book, obj, post or self._post(obj)
        )

    @pytest.mark.parametrize("cls,build,old,new,read", CASES)
    def test_keeps_remote_version_time(self, cls, build, old, new, read):
        self._apply(cls, build(1, old, T1))
        piece = cls.objects.get(owner=self.owner, item=self.book)
        assert piece.edited_time == datetime.fromisoformat(T1)

    @pytest.mark.parametrize("cls,build,old,new,read", CASES)
    def test_delayed_edit_applies(self, cls, build, old, new, read):
        # both versions predate their delivery here
        obj = build(1, old, T1)
        post = self._post(obj)
        self._apply(cls, obj, post)
        self._apply(cls, build(1, new, T2), post)
        assert read(cls.objects.get(owner=self.owner, item=self.book)) == new

    @pytest.mark.parametrize("cls,build,old,new,read", CASES)
    def test_older_version_from_another_post_ignored(self, cls, build, old, new, read):
        self._apply(cls, build(2, new, T2))
        # e.g. an older post refetched after takahe pruned it
        self._apply(cls, build(1, old, T1))
        assert read(cls.objects.get(owner=self.owner, item=self.book)) == new

    @pytest.mark.parametrize("cls,build,old,new,read", CASES)
    def test_legacy_row_keeps_old_behaviour(self, cls, build, old, new, read):
        self._apply(cls, build(1, old, T1))
        # a row saved before this change holds its local save time
        cls.objects.filter(owner=self.owner, item=self.book).update(
            edited_time=timezone.now()
        )
        self._apply(cls, build(2, new, T2))
        assert read(cls.objects.get(owner=self.owner, item=self.book)) == old

    def test_note_delayed_edit_applies(self):
        obj = {
            "id": "https://remote.example/note/1",
            "type": "Note",
            "content": "first",
            "published": T0,
            "updated": T1,
        }
        post = self._post(obj)
        Note.update_by_ap_object(self.owner, self.book, obj, post)
        note = Note.update_by_ap_object(
            self.owner, self.book, {**obj, "content": "second", "updated": T2}, post
        )
        assert note is not None
        note.refresh_from_db()
        assert note.content == "second"
        assert note.edited_time == datetime.fromisoformat(T2)
