import datetime
import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from django.utils import timezone

from catalog.models import Edition, Podcast, PodcastEpisode
from journal.models import Collection, Mark, ShelfType
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _member(username: str) -> tuple[User, Client]:
    user = User.register(email=f"{username}@example.com", username=username)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, client


def test_profile_page_sections_keep_ids_and_use_modules():
    user, client = _member("modules")
    content = client.get(user.identity.url).content.decode()

    for section_id in ("calendar_grid", "book_complete", "collection_created"):
        assert re.search(
            rf'<section class="entity-sort dc-mod"\s+id="{section_id}"', content
        )
    assert 'id="layoutEditButton"' in content
    assert (
        "<h5"
        not in content.split('<div class="grid__main">', 1)[1].split("<aside", 1)[0]
    )


def test_shelf_items_render_cover_cards_with_rating_and_count():
    user, client = _member("shelfcards")
    book = Edition.objects.create(title="Card Book", author=["Card Author"])
    book.sync_credits_from_metadata()
    # five ratings reach the threshold that shows an average
    for i in range(5):
        rater = User.register(email=f"rater{i}@example.com", username=f"rater{i}")
        Mark(rater.identity, book).update(ShelfType.COMPLETE, rating_grade=8)
    Mark(user.identity, book).update(ShelfType.COMPLETE, rating_grade=9)

    url = reverse(
        "journal:profile_shelf_items", args=[user.identity.handle, "book", "complete"]
    )
    content = client.get(url).content.decode()

    assert 'class="dc-mh"' in content
    assert ">1</a>" in content
    assert 'class="dc-card"' in content
    assert "Card Author" in content
    # the owner's own stars, not the public average, and not hidden from them
    assert "★ 8.0" not in content
    assert 'class="dc-badge dc-badge-stars">' in content
    assert 'data-rating="9.0"' in content

    # a visitor sees the owner's stars as another member's rating
    _, visitor = _member("shelfvisitor")
    assert (
        'class="dc-badge dc-badge-stars solo-hidden">'
        in visitor.get(url).content.decode()
    )


def test_listening_shelf_plays_the_newest_episode_of_each_show():
    user, client = _member("listener")
    show = Podcast.objects.create(title="Nightly Show")
    PodcastEpisode.objects.create(
        title="Older one",
        program=show,
        pub_date=timezone.now() - datetime.timedelta(days=1),
        media_url="https://example.org/old.mp3",
    )
    newest = PodcastEpisode.objects.create(
        title="Newest one",
        program=show,
        pub_date=timezone.now(),
        media_url="https://example.org/new.mp3",
    )
    silent = Podcast.objects.create(title="Silent Show")
    Mark(user.identity, show).update(ShelfType.PROGRESS)
    Mark(user.identity, silent).update(ShelfType.PROGRESS)

    # the episodes left the sidebar; the main shelf plays them instead
    home = client.get(user.identity.url).content.decode()
    assert "Recent podcast episodes" not in home.split("<aside", 1)[1]
    assert "js/podcast.js" in home

    url = reverse(
        "journal:profile_shelf_items",
        args=[user.identity.handle, "podcast", "progress"],
    )
    content = client.get(url).content.decode()

    assert f'data-media="{newest.media_url}"' in content
    assert "Older one" not in content
    # a show with no episode yet keeps its cover card, so the count still matches
    assert ">2</a>" in content
    assert silent.display_title in content


def test_created_collections_render_cover_mosaics():
    user, client = _member("mosaics")
    books = [Edition.objects.create(title=f"Mosaic Book {i}") for i in range(4)]
    full = Collection.objects.create(owner=user.identity, title="Four", visibility=0)
    for book in books:
        full.append_item(book)
    small = Collection.objects.create(owner=user.identity, title="One", visibility=0)
    small.append_item(books[0])

    url = reverse("journal:profile_created_collections", args=[user.identity.handle])
    content = client.get(url).content.decode()

    assert 'class="dc-cards dc-cols"' in content
    assert content.count('class="dc-mosaic"') == 1
    assert content.count('class="dc-mosaic single"') == 1
    assert "4 items" in content
    assert "1 item" in content
    # the default cover is square and fills the box, so it gets no backdrop
    assert 'class="dc-blur"' not in content


def test_collection_with_its_own_cover_keeps_it():
    user, client = _member("owncover")
    books = [Edition.objects.create(title=f"Cover Book {i}") for i in range(4)]
    own = Collection.objects.create(owner=user.identity, title="Mine", visibility=0)
    for book in books:
        own.append_item(book)
    own.cover.name = "collections/mine.png"
    own.save(update_fields=["cover"])

    Collection.attach_cover_previews([own])

    # four members, but the owner's cover stands instead of a mosaic of them
    assert own.cover_previews == []
    assert own.member_count == 4

    url = reverse("journal:profile_created_collections", args=[user.identity.handle])
    content = client.get(url).content.decode()
    assert 'class="dc-mosaic"' not in content
    assert 'class="dc-mosaic single"' in content
    # the whole cover shows, so a blurred copy of it fills the band each side
    assert content.count('class="dc-blur"') == 1


def test_attach_cover_previews_query_count_is_flat():
    user, _ = _member("previews")
    books = [Edition.objects.create(title=f"Preview Book {i}") for i in range(6)]
    collections = []
    for n in (6, 2, 0):
        c = Collection.objects.create(owner=user.identity, title=f"C{n}", visibility=0)
        for book in books[:n]:
            c.append_item(book)
        collections.append(c)

    with CaptureQueriesContext(connection) as one:
        Collection.attach_cover_previews(collections[:1])
    with CaptureQueriesContext(connection) as three:
        Collection.attach_cover_previews(collections)

    # members, their items (base row plus one per concrete class) and counts:
    # the same handful of queries however many collections are passed
    assert len(three.captured_queries) == len(one.captured_queries) <= 6
    assert [len(c.cover_previews) for c in collections] == [4, 2, 0]
    assert [c.member_count for c in collections] == [6, 2, 0]
    assert collections[0].cover_previews[0] == books[0].display_cover_image_url
