from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import Edition, ItemCategory, Movie
from journal.models import Tag, TagManager
from journal.models.tag import TagMember
from users.models import User


@pytest.mark.django_db(databases="__all__")
def test_indexable_tags_for_item_aggregates_public_tags():
    """Public tags are aggregated across users, cleaned and de-duplicated;
    private (visibility != 0) tags are excluded. This single-item primitive
    feeds the search index and the item detail page/API."""
    owner = User.register(email="tagger@example.com", username="tagger")
    other = User.register(email="tagger2@example.com", username="tagger2")
    tagged = Edition.objects.create(title="Tagged Book")
    untagged = Edition.objects.create(title="Untagged Book")

    tag = Tag.objects.create(owner=owner.identity, title="Sci-Fi", visibility=0)
    dup = Tag.objects.create(owner=other.identity, title="sci fi", visibility=0)
    hidden = Tag.objects.create(owner=other.identity, title="Hidden", visibility=1)
    tag.append_item(tagged)
    dup.append_item(tagged)
    hidden.append_item(tagged)

    # "Sci-Fi" and "sci fi" both clean to "sci fi"; "Hidden" is private.
    assert TagManager.indexable_tags_for_item(tagged) == ["sci fi"]
    assert TagManager.indexable_tags_for_item(untagged) == []


@pytest.mark.django_db(databases="__all__")
def test_item_tags_not_aggregated_on_read():
    """Item.tags must not auto-aggregate on read (NEODB-SOCIAL-7KW): a freshly
    loaded item exposes tags as None and touches no journal_tagmember row, so
    list/feed surfaces that never attach tags cannot trigger the slow query."""
    owner = User.register(email="noagg@example.com", username="noagg")
    book = Edition.objects.create(title="No-Aggregation Book")
    Tag.objects.create(owner=owner.identity, title="public", visibility=0).append_item(
        book
    )

    fresh = Edition.objects.get(pk=book.pk)
    with CaptureQueriesContext(connection) as ctx:
        tags = fresh.tags
    assert tags is None
    assert [q for q in ctx.captured_queries if "journal_tagmember" in q["sql"]] == []


@pytest.mark.django_db(databases="__all__")
def test_to_indexable_doc_includes_public_tags():
    """The public-tag aggregation runs at index time so read paths can reuse the
    indexed value; to_indexable_doc must carry the cleaned, public-only tags."""
    owner = User.register(email="idxtag@example.com", username="idxtag")
    book = Edition.objects.create(title="Indexed Tag Book")
    Tag.objects.create(owner=owner.identity, title="Sci-Fi", visibility=0).append_item(
        book
    )
    Tag.objects.create(owner=owner.identity, title="secret", visibility=1).append_item(
        book
    )

    doc = Edition.objects.get(pk=book.pk).to_indexable_doc()
    assert doc["tag"] == ["sci fi"]


@pytest.mark.django_db(databases="__all__")
def test_append_item_recovers_from_duplicate_race():
    """A concurrent insert that wins the parent+item unique race must not
    surface to the caller — append_item is idempotent (Sentry NEODB-SOCIAL-3JG)."""
    owner = User.register(email="race@example.com", username="raceowner")
    book = Edition.objects.create(title="Raced Book")
    tag = Tag.objects.create(owner=owner.identity, title="raced", visibility=0)

    winner, created = tag.append_item(book)
    assert created
    assert winner is not None

    # Simulate the race: only the *pre-check* `get_member_for_item` sees
    # None (as a losing transaction would before the winner committed) —
    # the post-IntegrityError recovery call must still see the row.
    real_get = Tag.get_member_for_item
    call_count = {"n": 0}

    def stubbed(self_inst, item):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None
        return real_get(self_inst, item)

    with patch.object(Tag, "get_member_for_item", autospec=True, side_effect=stubbed):
        recovered, created_again = tag.append_item(book)
    assert created_again is False
    assert recovered.pk == winner.pk
    assert TagMember.objects.filter(parent=tag, item=book).count() == 1


@pytest.mark.django_db(databases="__all__")
def test_get_tags_filtered_by_category():
    """get_tags(category=...) narrows tags to those with members in the
    category, and counts only members of that category."""
    owner = User.register(email="cat@example.com", username="catowner")
    book = Edition.objects.create(title="A Book")
    movie = Movie.objects.create(title="A Movie")

    shared = Tag.objects.create(owner=owner.identity, title="shared", visibility=0)
    book_only = Tag.objects.create(owner=owner.identity, title="bookonly", visibility=0)
    shared.append_item(book)
    shared.append_item(movie)
    book_only.append_item(book)

    mgr = owner.identity.tag_manager

    all_tags = {t.title: t.total for t in mgr.get_tags()}
    assert all_tags == {"shared": 2, "bookonly": 1}

    book_tags = {t.title: t.total for t in mgr.get_tags(category=ItemCategory.Book)}
    assert book_tags == {"shared": 1, "bookonly": 1}

    movie_tags = {t.title: t.total for t in mgr.get_tags(category=ItemCategory.Movie)}
    assert movie_tags == {"shared": 1}


@pytest.mark.django_db(databases="__all__")
def test_tag_item_for_owner_is_idempotent():
    """Repeated tag_item_for_owner calls must not raise on the Tag
    (owner, title) or TagMember (parent, item) unique constraints."""
    owner = User.register(email="idem@example.com", username="idemowner")
    book = Edition.objects.create(title="Idempotent Book")

    TagManager.tag_item_for_owner(owner.identity, book, ["alpha", "beta"])
    TagManager.tag_item_for_owner(owner.identity, book, ["alpha", "beta"])

    assert Tag.objects.filter(owner=owner.identity).count() == 2
    assert TagMember.objects.filter(owner=owner.identity, item=book).count() == 2
