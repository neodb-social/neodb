import base64
import datetime
import io
import json
import re
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from catalog.models import Edition, Movie, TVSeason, TVShow
from journal.jobs import wrapped as wrapped_jobs
from journal.jobs.wrapped import (
    WrappedDailyUpdate,
    WrappedInstanceStats,
    enqueue_deck_refresh,
    refresh_state,
)
from journal.models import (
    Collection,
    CollectionMember,
    Comment,
    Like,
    Note,
    Rating,
    Review,
    ShelfLogEntry,
    ShelfMember,
    ShelfType,
    Tag,
    TagMember,
    WrappedDeck,
)
from journal.wrapped.aggregates import (
    _instance_key,
    _median,
    _percentile,
    compute_instance_stats,
    compute_kindred,
    instance_stats,
    year_aggregates,
)
from journal.wrapped.base import (
    BULK_DAY,
    _ctype_categories,
    plain_excerpt,
    year_range,
)
from journal.wrapped.data import (
    _reacted,
    build_payload,
    live_year,
)
from journal.wrapped.record import (
    deck_for,
    friends_allowed,
    kindred_allowed,
    refresh_deck,
    snapshot,
    stored_payload,
)
from journal.wrapped.words import (
    resolve_words,
)
from journal.models.wrapped import MADE_CARDS_MAX
from journal.wrapped.music import track as music_track
from journal.wrapped.text import PLURALS, TEXT, card_text
from takahe.models import Domain
from takahe.models import Identity as TakaheIdentity
from takahe.models import Post as TakahePost
from takahe.utils import Takahe
from users.jobs.cleanup import clear_identity_data
from users.models import APIdentity, User

TZ = ZoneInfo("Asia/Shanghai")
QUIET = {"post_when_save": False, "index_when_save": False}
# a 1 x 1 PNG that decodes, for the stills and images the share view checks
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
YEAR = 2025


@pytest.fixture(autouse=True)
def wrapped_cache(settings):
    """A cache of this module's own: the tests would otherwise share redis with the dev cluster, and
    clear or fill the cached instance totals and kindred matches its pages read."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "wrapped-tests",
        }
    }
    cache.clear()


@pytest.fixture(autouse=True)
def _clear_circles_cache(wrapped_cache):
    """Takes the place of the conftest fixture of this name, which clears reco keys in the shared redis:
    here the cache is this module's own and was just cleared, so no fixture of this module touches redis.
    The class fixtures that build data ask for ``wrapped_cache`` too, so it always runs first."""


class DeckJobs:
    """Stands in for the queue of the jobs that make and update decks: a job runs at once, or waits while
    ``hold`` is set until ``run()``. The tests share redis with the dev cluster, whose worker would otherwise
    run a queued job against its own database."""

    def __init__(self) -> None:
        self.queues: list[str] = []
        self.held: list[tuple[Any, tuple]] = []
        self.hold = False

    def get_queue(self, name: str) -> "DeckJobs":
        self.queues.append(name)
        return self

    def enqueue(self, func: Any, *args: Any) -> None:
        self.held.append((func, args))
        if not self.hold:
            self.run()

    def run(self) -> None:
        while self.held:
            func, args = self.held.pop(0)
            func(*args)


@pytest.fixture(autouse=True)
def deck_jobs(monkeypatch) -> DeckJobs:
    jobs = DeckJobs()
    monkeypatch.setattr(wrapped_jobs, "django_rq", jobs)
    return jobs


def clear_wrapped_cache() -> None:
    cache.clear()


def make_deck(identity, year: int = YEAR, **fields) -> WrappedDeck:
    """The deck as the step that makes it leaves it: the row, then its card data stored."""
    deck = deck_for(identity, year, create=True)
    assert deck is not None
    for k, v in fields.items():
        setattr(deck, k, v)
    if fields:
        deck.save()
    refresh_deck(deck)
    return deck


def on(day: datetime.date):
    """The site's date for the window in which a deck can be made."""
    return patch("journal.wrapped.record._today", return_value=day)


def at(month: int, day: int, hour: int = 12, year: int = YEAR) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, tzinfo=TZ)


def complete(identity, item, when, vis=0, grade=None, comment=None, tags=()):
    shelf = identity.shelf_manager.get_shelf(ShelfType.COMPLETE)
    ShelfMember(
        owner=identity,
        item=item,
        parent=shelf,
        visibility=vis,
        created_time=when,
        position=0,
    ).save(**QUIET)
    ShelfLogEntry.objects.create(
        owner=identity, item=item, shelf_type=ShelfType.COMPLETE, timestamp=when
    )
    if grade:
        Rating(
            owner=identity, item=item, grade=grade, visibility=vis, created_time=when
        ).save(**QUIET)
    if comment:
        Comment(
            owner=identity, item=item, text=comment, visibility=vis, created_time=when
        ).save(**QUIET)
    for t in tags:
        tag = Tag.objects.filter(owner=identity, title=t).first()
        if tag is None:
            tag = Tag(owner=identity, title=t, visibility=0)
            tag.save(**QUIET)
        TagMember(
            owner=identity,
            parent=tag,
            item=item,
            visibility=vis,
            created_time=when,
            position=0,
        ).save(**QUIET)


@pytest.mark.django_db(databases="__all__")
class TestPayload:
    @pytest.fixture(autouse=True)
    def setup_data(self, wrapped_cache):
        clear_wrapped_cache()
        self.user = User.register(email="wrap@test.com", username="wrapper")
        self.me = self.user.identity
        self.public = Edition.objects.create(title="Public Book")
        self.followers = Movie.objects.create(title="Followers Film")
        self.private = Edition.objects.create(title="Private Book")
        complete(self.me, self.public, at(3, 1), 0, 10, "a public note", ["comfort"])
        complete(
            self.me, self.followers, at(3, 2), 1, 4, "for followers", ["secret tag"]
        )
        complete(self.me, self.private, at(3, 3), 2, 6, "only for me")

    def titles(self, level):
        return [i["title"] for i in build_payload(self.me, YEAR, level, TZ)["items"]]

    def test_levels_include_only_visible_marks(self):
        assert self.titles(0) == ["Public Book"]
        assert self.titles(1) == ["Public Book", "Followers Film"]
        assert self.titles(2) == ["Public Book", "Followers Film", "Private Book"]

    def test_a_title_finished_before_counts_its_earlier_days(self):
        for when, meta in (
            (at(6, 1, year=2019), {}),
            (at(6, 1, year=2021), {}),
            # the same day as this year's finish, and a progress update, are no earlier finish
            (at(3, 1, hour=8), {}),
            (at(1, 9, year=2023), {"progress_type": "page", "progress_value": "10"}),
        ):
            ShelfLogEntry.objects.create(
                owner=self.me,
                item=self.public,
                shelf_type=ShelfType.COMPLETE,
                timestamp=when,
                metadata=meta,
            )
        items = {i["title"]: i for i in build_payload(self.me, YEAR, 2, TZ)["items"]}
        again = items.pop("Public Book")
        assert (again["done_before"], again["first_done"]) == (2, "2019-06-01")
        assert all(i["done_before"] == 0 for i in items.values())
        # the whole-year section carries only what its counts and piles need
        live = live_year(self.me, YEAR, 2, TZ)["items"]
        assert [i["title"] for i in live] == [
            "Public Book",
            "Followers Film",
            "Private Book",
        ]
        assert set(live[0]) == {
            "uuid",
            "title",
            "category",
            "cover",
            "url",
            "rating",
            "date",
        }

    def test_clearing_the_account_removes_its_decks(self):
        make_deck(self.me)
        make_deck(self.me, YEAR - 1)
        other = make_deck(
            User.register(email="keep@test.com", username="keep").identity
        )
        # what the account deletion queues; the identity row stays, so no cascade reaches the decks
        clear_identity_data(self.me.pk)
        assert APIdentity.objects.filter(pk=self.me.pk).exists()
        assert list(WrappedDeck.objects.values_list("pk", flat=True)) == [other.pk]

    def test_public_payload_carries_no_private_words(self):
        dump = json.dumps(build_payload(self.me, YEAR, 0, TZ), ensure_ascii=False)
        for word in (
            "for followers",
            "only for me",
            "secret tag",
            "Followers Film",
            "Private Book",
        ):
            assert word not in dump

    def test_item_fields(self):
        it = build_payload(self.me, YEAR, 0, TZ)["items"][0]
        assert it["rating"] == 10 and it["comment"] == "a public note"
        assert it["tags"] == ["comfort"]
        assert it["date"] == "2025-03-01" and it["category"] == "book"
        assert it["first_rank"] == 1 and it["marks"] == 1

    def test_activity_follows_mark_visibility(self):
        def kinds(lv: int) -> list[str]:
            return sorted(
                a["date"] for a in build_payload(self.me, YEAR, lv, TZ)["activity"]
            )

        assert kinds(0) == ["2025-03-01"]
        assert kinds(2) == ["2025-03-01", "2025-03-02", "2025-03-03"]

    def test_year_is_cut_in_the_deck_time_zone(self):
        late = Edition.objects.create(title="New Year Book")
        # 20:00 UTC on Dec 31 is already Jan 1 in Shanghai
        complete(
            self.me, late, datetime.datetime(2025, 12, 31, 20, tzinfo=datetime.UTC)
        )
        assert "New Year Book" not in self.titles(0)
        assert "New Year Book" in [
            i["title"]
            for i in build_payload(self.me, YEAR, 0, ZoneInfo("UTC"))["items"]
        ]
        assert "New Year Book" in [
            i["title"] for i in build_payload(self.me, YEAR + 1, 0, TZ)["items"]
        ]

    def test_wishlist_date_only_when_before_completion(self):
        ShelfLogEntry.objects.create(
            owner=self.me,
            item=self.public,
            shelf_type=ShelfType.WISHLIST,
            timestamp=at(1, 5, year=2020),
        )
        ShelfLogEntry.objects.create(
            owner=self.me,
            item=self.followers,
            shelf_type=ShelfType.WISHLIST,
            timestamp=at(6, 1),
        )
        items = {i["title"]: i for i in build_payload(self.me, YEAR, 1, TZ)["items"]}
        assert items["Public Book"]["wishlisted"] == "2020-01-05"
        assert items["Followers Film"]["wishlisted"] is None

    def test_history_splits_bulk_days(self):
        for k in range(BULK_DAY + 2):
            complete(
                self.me,
                Edition.objects.create(title=f"Import {k}"),
                at(5, 7, year=2024),
            )
        complete(self.me, Edition.objects.create(title="Ordinary"), at(6, 1, year=2024))
        h = build_payload(self.me, YEAR, 0, TZ)["history"]
        y = h["years"]["2024"]
        assert y["b"][4] == BULK_DAY + 2 and y["m"][4] == 0 and y["m"][5] == 1
        assert y["d"][5] == 1
        assert build_payload(self.me, YEAR, 0, TZ)["last_year"] == {
            "book": BULK_DAY + 3
        }

    def test_reviews_follow_visibility_and_strip_spoilers(self):
        Review(
            owner=self.me,
            item=self.public,
            title="Long one",
            body="Hello **world** >!the twist!< and more",
            visibility=0,
            created_time=at(4, 1),
        ).save(**QUIET)
        Review(
            owner=self.me,
            item=self.private,
            title="Hidden one",
            body="secret",
            visibility=2,
            created_time=at(4, 2),
        ).save(**QUIET)
        rv = build_payload(self.me, YEAR, 0, TZ)["reviews"]
        assert [r["title"] for r in rv] == ["Long one"]
        assert "twist" not in rv[0]["excerpt"] and rv[0]["excerpt"].startswith(
            "Hello world"
        )

    def test_plain_excerpt(self):
        assert (
            plain_excerpt("# Title\n![img](x.png) [link](http://a) >!spoiler!< end")
            == "Title link end"
        )
        assert plain_excerpt("before >!unclosed spoiler") == "before"
        assert plain_excerpt("a >!b!< c >!d!< e") == "a c e"

    def test_plain_excerpt_stays_linear_on_unclosed_marks(self):
        # each unclosed mark made the old patterns scan to the end again: about 3 s for 40,000 characters
        for text in (">!" * 20000, "![" * 20000, "[a](" * 10000, "<" * 40000):
            started = time.perf_counter()
            plain_excerpt("start " + text, 2000)
            assert time.perf_counter() - started < 0.5


@pytest.mark.django_db(databases="__all__")
class TestFriends:
    @pytest.fixture(autouse=True)
    def setup_data(self, wrapped_cache):
        clear_wrapped_cache()
        self.me = User.register(email="f1@test.com", username="fme").identity
        self.pal = User.register(email="f2@test.com", username="fpal").identity
        self.shy = User.register(email="f3@test.com", username="fshy").identity
        self.shy.anonymous_viewable = False
        self.shy.save()
        Takahe.follow(self.me.pk, self.pal.pk, force_accept=True)
        Takahe.follow(self.me.pk, self.shy.pk, force_accept=True)
        self.shared = Edition.objects.create(title="Shared")
        self.other = Edition.objects.create(title="Other")
        complete(self.me, self.shared, at(2, 1))
        complete(self.pal, self.shared, at(1, 1))
        complete(self.pal, self.other, at(1, 2), vis=0)
        complete(self.shy, self.shared, at(1, 3))

    def test_only_public_titles_and_members_hidden_from_visitors_still_count(self):
        friends = {
            f["handle"].split("@")[0]: f
            for f in build_payload(self.me, YEAR, 0, TZ)["friends"]
        }
        # fshy hides their own pages from signed-out visitors, which does not keep them off this deck
        assert sorted(friends) == ["fpal", "fshy"]
        assert friends["fpal"]["n"] == 2
        assert [i["u"] for i in friends["fpal"]["items"]] == [self.shared.url]
        assert [i["u"] for i in friends["fshy"]["items"]] == [self.shared.url]

    def test_visitors_get_friends_only_when_a_social_card_can_show(self):
        deck = WrappedDeck.objects.create(owner=self.me, year=YEAR)
        assert friends_allowed(deck, 0, True)
        # an audience the owner has not opened: the rules may show a social card, unless the member hid them
        assert friends_allowed(deck, 0, False)
        deck.hidden = {"0": {"*": True}}
        assert not friends_allowed(deck, 0, False)
        deck.hidden = {"0": {"*": True, "twin": False}}
        assert friends_allowed(deck, 0, False)
        # an audience the owner opened: only when its list has a social card
        deck.hidden = {}
        deck.shown = {"0": ["intro", "shelves"]}
        assert not friends_allowed(deck, 0, False)
        deck.shown = {"0": ["intro", "twin"]}
        assert friends_allowed(deck, 0, False)
        # the followers' audience is not opened yet, so its rules decide
        assert friends_allowed(deck, 1, False)
        assert (
            build_payload(self.me, YEAR, 0, TZ, include_friends=False)["friends"] == []
        )


@pytest.mark.django_db(databases="__all__")
class TestKindred:
    """A member the owner does not follow whose public year overlaps the owner's the most."""

    @pytest.fixture(autouse=True)
    def setup_data(self, wrapped_cache):
        clear_wrapped_cache()
        self.me = self.member("kme")
        self.rare = self.member("krare")
        self.heavy = self.member("kheavy")
        self.fill = [self.member(f"kfill{k}") for k in range(4)]
        self.rare_titles = [Edition.objects.create(title=f"Rare {k}") for k in range(5)]
        self.popular = [Edition.objects.create(title=f"Popular {k}") for k in range(8)]
        # five titles only one other member finished, and eight that six members finished
        for k, it in enumerate(self.rare_titles):
            complete(self.me, it, at(3, 1 + k))
            complete(self.rare, it, at(4, 1 + k))
        for k, it in enumerate(self.popular):
            for who in [self.me, self.heavy, *self.fill]:
                complete(who, it, at(5, 1 + k))
        for k in range(20):
            complete(
                self.heavy, Edition.objects.create(title=f"Heavy {k}"), at(6, 1 + k)
            )
        yield
        # the matches hold this test's member ids, and redis is shared with the dev cluster
        clear_wrapped_cache()

    def member(self, name):
        return User.register(email=f"{name}@kindred.test", username=name).identity

    def kindred(self):
        return build_payload(self.me, YEAR, 2, TZ, include_kindred=True)["kindred"]

    def test_a_shared_rare_title_counts_for_more(self):
        best = compute_kindred(YEAR)[self.me.pk]
        assert best[0][0] == self.rare.pk and best[0][2] == 5
        # eight popular titles in common still rank below five rare ones
        assert self.heavy.pk in [b for b, _, _ in best]

    def test_hidden_deleted_private_and_bulk_day_members_are_left_out(self):
        TakaheIdentity.objects.filter(pk=self.rare.pk).update(discoverable=False)
        APIdentity.objects.filter(pk=self.heavy.pk).update(deleted=timezone.now())
        shy, bulk = self.member("kshy"), self.member("kbulk")
        for it in self.rare_titles:
            complete(shy, it, at(7, 1), vis=1)
        # thirty-one titles on one day is an import, so none of them count
        for it in self.rare_titles + [
            Edition.objects.create(title=f"B{k}") for k in range(26)
        ]:
            complete(bulk, it, at(8, 2))
        ids = {b for b, _, _ in compute_kindred(YEAR).get(self.me.pk, [])}
        assert not ids & {self.rare.pk, self.heavy.pk, shy.pk, bulk.pk}
        assert {f.pk for f in self.fill} <= ids

    def test_a_remote_member_can_match_but_gets_no_card(self):
        domain, _ = Domain.objects.get_or_create(
            domain="kindred.example", defaults={"local": False}
        )
        far = Takahe.get_or_create_remote_apidentity(
            TakaheIdentity.objects.create(
                actor_uri="https://kindred.example/users/far/",
                local=False,
                username="far",
                domain=domain,
            )
        )
        for k, it in enumerate(self.rare_titles):
            complete(far, it, at(9, 1 + k))
        matches = compute_kindred(YEAR)
        assert far.pk in [b for b, _, _ in matches[self.me.pk]]
        assert far.pk not in matches

    def test_the_page_skips_followed_blocked_and_muted_members(self):
        compute_kindred(YEAR)
        k = self.kindred()
        assert k["handle"].split("@")[0] == "krare" and k["url"] == self.rare.url
        assert k["n"] == 5 and len(k["items"]) == 5
        by_name = {i.username: i for i in [self.heavy, *self.fill]}
        Takahe.follow(self.me.pk, self.rare.pk, force_accept=True)
        second = self.kindred()["handle"].split("@")[0]
        assert second in by_name
        # a block in either direction and the owner's mute each move on to the next match
        Takahe.block_or_mute(by_name[second].pk, self.me.pk, False)
        third = self.kindred()["handle"].split("@")[0]
        assert third in by_name and third != second
        Takahe.block_or_mute(self.me.pk, by_name[third].pk, True)
        fourth = self.kindred()["handle"].split("@")[0]
        assert fourth not in (second, third, "krare")
        assert build_payload(self.me, YEAR, 2, TZ)["kindred"] is None

    def test_only_the_owner_gets_the_data_until_an_audience_is_shown_it(self):
        deck = WrappedDeck.objects.create(owner=self.me, year=YEAR)
        assert kindred_allowed(deck, 0, True)
        assert not kindred_allowed(deck, 0, False) and not kindred_allowed(
            deck, 1, False
        )
        deck.hidden = {"0": {"kindred": False}}
        assert kindred_allowed(deck, 0, False) and not kindred_allowed(deck, 1, False)
        deck.shown = {"1": ["intro", "kindred"]}
        assert kindred_allowed(deck, 1, False)
        assert not kindred_allowed(None, 0, False)
        # a signed-out visitor's page carries no match even when the stored data has one
        compute_kindred(YEAR)
        deck.hidden, deck.shown = {}, {}
        deck.save()
        refresh_deck(deck)
        assert deck.data["0"]["kindred"]["handle"].startswith("krare")
        r = Client().get(reverse("journal:wrapped_page", args=["kme", YEAR]))
        assert r.status_code == 200 and b"krare" not in r.content

    def test_turning_the_card_on_for_everyone_reaches_signed_out_visitors(self):
        compute_kindred(YEAR)
        make_deck(self.me)
        owner = Client()
        owner.force_login(self.me.user)
        r = owner.post(
            reverse("journal:wrapped_save", args=[YEAR]),
            json.dumps({"aud": 0, "hidden": {"kindred": False}}),
            content_type="application/json",
        )
        assert r.status_code == 200
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert deck.card_hidden(0, "kindred") is False
        page = Client().get(reverse("journal:wrapped_page", args=["kme", YEAR]))
        assert b"krare" in page.content
        # a member who stops being discoverable leaves the stored card at once
        krare = APIdentity.objects.get(username="krare", local=True)
        TakaheIdentity.objects.filter(pk=krare.pk).update(discoverable=False)
        page = Client().get(reverse("journal:wrapped_page", args=["kme", YEAR]))
        assert b"krare" not in page.content
        TakaheIdentity.objects.filter(pk=krare.pk).update(discoverable=True)
        # followers were not shown it, so their page still carries no match
        assert not kindred_allowed(deck, 1, False)


def page_data(response) -> dict:
    m = re.search(
        rb'<script id="wr-data" type="application/json">(.*?)</script>',
        response.content,
        re.S,
    )
    assert m, "no page data"
    return json.loads(m.group(1))


def deck_url(username: str, year: int = YEAR) -> str:
    return reverse("journal:wrapped_page", args=[username, year])


@pytest.mark.django_db(databases="__all__")
class TestReviewFixes:
    """What the branch review found: the visitor payload, saves, and the data the cards draw from."""

    @pytest.fixture(autouse=True)
    def setup_data(self, wrapped_cache):
        clear_wrapped_cache()
        self.user = User.register(email="rf@test.com", username="rfowner")
        self.me = self.user.identity
        self.book = Edition.objects.create(title="Reviewed Book")
        complete(
            self.me, self.book, at(2, 2), grade=8, comment="good >!the twist!< ending"
        )
        self.owner = Client()
        self.owner.force_login(self.user)

    def words_card(self, card_id):
        c = Comment.objects.get(owner=self.me, item=self.book)
        return {
            "id": card_id,
            "title": "",
            "caption": "",
            "n": 3,
            "source": {
                "type": "words",
                "url": self.book.absolute_url,
                "kind": "comment",
                "ref": str(c.uid),
            },
        }

    def test_comments_lose_spoilers_and_ratings_count_raters(self):
        it = build_payload(self.me, YEAR, 2, TZ)["items"][0]
        assert "twist" not in it["comment"] and "good" in it["comment"]
        assert it["raters"] == 1

    def test_a_hidden_words_card_keeps_its_text_on_the_server(self):
        deck = make_deck(
            self.me,
            made=[self.words_card(7)],
            hidden={"0": {"made-7": True}},
        )
        url = reverse("journal:wrapped_page", args=["rfowner", YEAR])
        assert page_data(Client().get(url))["words"] == {}
        deck.hidden = {}
        deck.save()
        assert list(page_data(Client().get(url))["words"]) == ["7"]

    def test_two_cards_on_one_text_both_resolve(self):
        words = resolve_words(self.me, [self.words_card(7), self.words_card(8)], 2, TZ)
        assert sorted(words) == ["7", "8"]

    def page_text(self, deck):
        url = reverse("journal:wrapped_page", args=["rfowner", YEAR])
        return json.dumps(page_data(Client().get(url))["collections"])

    def test_made_cards_keep_what_they_showed_when_saved(self):
        author = User.register(email="rfs@test.com", username="rfsaved").identity
        col = Collection(owner=author, title="Their List", visibility=0)
        col.save(**QUIET)
        listed = Edition.objects.create(title="Listed Book")
        CollectionMember(owner=author, parent=col, item=listed, position=1).save(
            **QUIET
        )
        card = {"id": 8, "n": 3, "source": {"type": "collection", "uuid": col.uuid}}
        deck = make_deck(self.me, made=[self.words_card(7), card])

        def shown():
            p = stored_payload(WrappedDeck.objects.get(pk=deck.pk), 0, viewer=self.user)
            cols = [
                (c["title"], [i["t"] for i in c["items"]]) for c in p["collections"]
            ]
            return cols, {k: w["text"] for k, w in p["words"].items()}

        before = shown()
        assert before[0] == [("Their List", ["Listed Book"])] and set(before[1]) == {
            "7"
        }
        assert "author_id" not in str(stored_payload(deck, 0)["collections"])
        # later changes to the collection, its items and the text do not reach the cards
        Collection.objects.filter(pk=col.pk).update(title="Renamed List")
        Edition.objects.filter(pk=listed.pk).update(is_deleted=True)
        Comment.objects.filter(owner=self.me, item=self.book).update(
            text="changed later"
        )
        assert shown() == before
        # a visitor gets the collection only while its card is shown to their audience
        assert "Their List" in self.page_text(deck)
        WrappedDeck.objects.filter(pk=deck.pk).update(hidden={"0": {"made-8": True}})
        assert "Their List" not in self.page_text(deck)
        WrappedDeck.objects.filter(pk=deck.pk).update(hidden={})
        # another member's collection made private leaves the cards at once, as their own content
        Collection.objects.filter(pk=col.pk).update(visibility=2)
        assert shown()[0] == [] and shown()[1] == before[1]
        # a saved change to the made cards stores what they show now
        r = self.owner.post(
            reverse("journal:wrapped_save", args=[YEAR]),
            json.dumps({"aud": 0, "made": [self.words_card(7)]}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert shown()[1] == {"7": "changed later"}

    def test_an_update_keeps_a_made_card_saved_while_it_ran(self):
        deck = make_deck(self.me)
        # an update that read the deck before the owner saved a new made card
        stale = WrappedDeck.objects.get(pk=deck.pk)
        r = self.owner.post(
            reverse("journal:wrapped_save", args=[YEAR]),
            json.dumps({"aud": 0, "made": [self.words_card(7)]}),
            content_type="application/json",
        )
        assert r.status_code == 200
        refresh_deck(stale)
        stored = WrappedDeck.objects.get(pk=deck.pk)
        assert set(stored.data["0"]["made_data"]["words"]) == {"7"}
        assert stale.made == stored.made

    def test_a_liked_collection_keeps_its_authors_rule(self):
        author = User.register(email="rfa@test.com", username="rfauthor").identity
        author.anonymous_viewable = False
        author.save()
        col = Collection(owner=author, title="Their List", visibility=0)
        col.save(**QUIET)
        Like.objects.create(owner=self.me, target=col)

        def titles(viewer):
            return [
                c["title"]
                for c in build_payload(self.me, YEAR, 0, TZ, viewer=viewer)[
                    "collections"
                ]
            ]

        # another member's collection is their own content: hidden from signed-out visitors as on its own page
        assert "Their List" not in titles(None)
        assert "Their List" in titles(self.user)

    def test_a_saved_collection_card_outlives_the_picker_window(self):
        oldest = Collection(owner=self.me, title="Oldest List", visibility=0)
        oldest.save(**QUIET)
        for k in range(30):
            Collection(owner=self.me, title=f"Newer {k}", visibility=0).save(**QUIET)

        def titles(refs):
            p = build_payload(self.me, YEAR, 0, TZ, collection_refs=refs)
            return [c["title"] for c in p["collections"]]

        assert "Oldest List" not in titles(None)
        assert "Oldest List" in titles([oldest.uuid])

    def test_friend_cards_pick_from_everyone_followed(self):
        idle = [
            User.register(email=f"rfi{k}@test.com", username=f"rfidle{k}").identity
            for k in range(3)
        ]
        busy = User.register(email="rfb@test.com", username="rfbusy").identity
        busier = User.register(email="rfb2@test.com", username="rfbusier").identity
        for p in idle + [busy, busier]:
            Takahe.follow(self.me.pk, p.pk, force_accept=True)
        complete(busy, self.book, at(3, 3))
        complete(busier, self.book, at(3, 4))
        complete(busier, Edition.objects.create(title="More"), at(3, 5))
        with patch("journal.wrapped.data.FRIENDS_MAX", 1):
            friends = build_payload(self.me, YEAR, 0, TZ)["friends"]
        # followed accounts with no completion this year never take the places of those with some
        assert [f["handle"].split("@")[0] for f in friends] == ["rfbusier"]

    def test_deleted_titles_stay_off_the_waiting_card(self):
        gone = Edition.objects.create(title="Gone Book")
        kept = Edition.objects.create(title="Still Here")
        shelf = self.me.shelf_manager.get_shelf(ShelfType.WISHLIST)
        for item, when in ((gone, at(1, 1, year=2020)), (kept, at(1, 1, year=2021))):
            ShelfMember(
                owner=self.me,
                item=item,
                parent=shelf,
                visibility=0,
                created_time=when,
                position=0,
            ).save(**QUIET)
        Edition.objects.filter(pk=gone.pk).update(is_deleted=True)
        assert build_payload(self.me, YEAR, 0, TZ)["waiting"]["title"] == "Still Here"

    def test_a_visitor_never_sees_people_they_block_or_moderated_accounts(self):
        pal = User.register(email="rfp@test.com", username="rfpal").identity
        Takahe.follow(self.me.pk, pal.pk, force_accept=True)
        complete(pal, self.book, at(3, 3))
        visitor = User.register(email="rfv@test.com", username="rfvisitor")

        def friends(viewer):
            return [
                f["handle"].split("@")[0]
                for f in build_payload(self.me, YEAR, 0, TZ, viewer=viewer)["friends"]
            ]

        assert friends(None) == ["rfpal"]
        Takahe.block_or_mute(visitor.identity.pk, pal.pk, False)
        assert friends(visitor) == []
        # a moderated account stays hidden from people who do not follow it
        TakaheIdentity.objects.filter(pk=pal.pk).update(restriction=1)
        assert friends(None) == []
        assert friends(self.user) == ["rfpal"]
        # the stored cards serve every visitor, so each page leaves out the people its own visitor must not see
        deck = make_deck(self.me)

        def stored(viewer):
            return stored_payload(deck, 0, viewer=viewer)["friends"]

        assert [f["handle"].split("@")[0] for f in stored(self.user)] == ["rfpal"]
        assert stored(None) == [] and stored(visitor) == []
        TakaheIdentity.objects.filter(pk=pal.pk).update(restriction=0)
        assert [f["handle"].split("@")[0] for f in stored(None)] == ["rfpal"]
        assert stored(visitor) == [] and "id" not in stored(None)[0]
        # people as they are now: not once the owner blocks them, nor once their account is gone
        Takahe.block_or_mute(self.me.pk, pal.pk, False)
        deck = WrappedDeck.objects.get(pk=deck.pk)
        assert stored(None) == []
        Takahe.unblock(self.me.pk, pal.pk)
        deck = WrappedDeck.objects.get(pk=deck.pk)
        assert len(stored(None)) == 1
        APIdentity.objects.filter(pk=pal.pk).update(deleted=timezone.now())
        assert stored(None) == []

    def test_an_early_place_is_kept_only_up_to_the_cap(self):
        popular = Edition.objects.create(title="Popular Book")
        second = Edition.objects.create(title="Second Book")
        for k in range(2):
            other = User.register(
                email=f"rfe{k}@test.com", username=f"rfearly{k}"
            ).identity
            complete(other, popular, at(1, k + 1))
            complete(other, self.book, at(3, k + 1))
        complete(other, second, at(1, 5))
        complete(self.me, popular, at(2, 3))
        complete(self.me, second, at(2, 4))
        with patch("journal.wrapped.data.FIRST_RANK_MAX", 2):
            items = build_payload(self.me, YEAR, 2, TZ)["items"]
        ranks = {i["title"]: i["first_rank"] for i in items}
        assert ranks == {"Reviewed Book": 1, "Second Book": 2, "Popular Book": None}

    def test_a_collection_card_without_a_collection_is_dropped(self):
        make_deck(self.me)
        made = [{"id": 1, "n": 5, "source": {"type": "collection", "uuid": ""}}]
        r = self.owner.post(
            reverse("journal:wrapped_save", args=[YEAR]),
            json.dumps({"aud": 0, "made": made}),
            content_type="application/json",
        )
        assert r.status_code == 200
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert deck.made == []
        # an id that does not decode, stored before this check, still lets the page open
        WrappedDeck.objects.filter(pk=deck.pk).update(
            made=made
            + [{"id": 2, "n": 5, "source": {"type": "collection", "uuid": "!"}}]
        )
        assert self.owner.get(deck_url("rfowner")).status_code == 200

    def test_a_partial_save_keeps_cards_the_page_lacked_but_not_hidden_ones(self):
        make_deck(self.me)
        url = reverse("journal:wrapped_save", args=[YEAR])

        def save(body):
            r = self.owner.post(url, json.dumps(body), content_type="application/json")
            assert r.status_code == 200
            return WrappedDeck.objects.get(owner=self.me, year=YEAR).shown_for(0)

        # with no saved list, an incomplete one is not saved
        assert save({"aud": 0, "shown": ["intro"], "partial": True}) is None
        assert save({"aud": 0, "shown": ["intro", "part", "made-7"]}) == [
            "intro",
            "part",
            "made-7",
        ]
        # the page lacked "part" while warming; "made-7" the owner hides now, so visitors lose it at once
        shown = save(
            {"aud": 0, "shown": ["intro"], "hidden": {"made-7": True}, "partial": True}
        )
        assert shown == ["intro", "part"]

    def test_saves_reject_more_made_cards_than_allowed(self):
        make_deck(self.me)
        url = reverse("journal:wrapped_save", args=[YEAR])
        made = [
            {"id": k + 1, "n": 5, "source": {"type": "auto", "cat": "all"}}
            for k in range(MADE_CARDS_MAX + 1)
        ]
        r = self.owner.post(
            url, json.dumps({"aud": 0, "made": made}), content_type="application/json"
        )
        assert r.status_code == 400
        r = self.owner.post(
            url,
            json.dumps({"aud": 0, "made": made[:-1]}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert (
            len(WrappedDeck.objects.get(owner=self.me, year=YEAR).made)
            == MADE_CARDS_MAX
        )

    def test_the_owner_opens_on_an_audience_with_titles(self):
        user = User.register(email="rfq@test.com", username="rfquiet")
        complete(
            user.identity, Edition.objects.create(title="Quiet Book"), at(2, 3), vis=1
        )
        make_deck(user.identity)
        c = Client()
        c.force_login(user)
        assert page_data(c.get(deck_url("rfquiet")))["level"] == 1
        empty = page_data(c.get(deck_url("rfquiet") + "?as=0"))
        assert empty["items"] == [] and empty["levels_with_titles"] == [1, 2]

    def test_old_years_open(self):
        assert (
            self.owner.get(reverse("journal:wrapped", args=[1999])).status_code == 200
        )

    def test_bulk_day_ratings_stay_out_of_the_history(self):
        for k in range(31):
            complete(
                self.me,
                Edition.objects.create(title=f"Import {k}"),
                at(5, 5, year=YEAR - 1),
                grade=10,
            )
        year = build_payload(self.me, YEAR, 2, TZ)["history"]["years"][str(YEAR - 1)]
        assert sum(year["r"]) == 0 and sum(year["f"]) == 0 and sum(year["b"]) == 31

    def test_a_non_public_video_post_reaches_mastodon_as_a_copy_with_its_still(self):
        make_deck(self.me)
        mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 100
        png = PNG
        with (
            patch("journal.views.wrapped.Takahe.upload_attachment"),
            patch("journal.views.wrapped.Takahe.post") as post,
            patch("users.models.User.mastodon", new_callable=PropertyMock) as mastodon,
            patch(
                "users.models.User.bluesky",
                new_callable=PropertyMock,
                return_value=None,
            ),
        ):
            post.return_value.url = "https://example.org/p/1"
            r = self.owner.post(
                reverse("journal:wrapped_share", args=[YEAR]),
                {
                    "kind": "video",
                    "comment": "my year",
                    "visibility": 1,
                    "aud": 0,
                    "crosspost": "1",
                    "video": SimpleUploadedFile("v.mp4", mp4, "video/mp4"),
                    "poster": SimpleUploadedFile("p.png", png, "image/png"),
                },
                HTTP_ACCEPT="application/json",
            )
        assert r.status_code == 200
        account = mastodon.return_value
        # a followers-only post cannot be boosted, so it goes as a copy, with the video's still
        assert account.post.call_args.kwargs["attachments"] == [
            (f"{YEAR}-1.png", png, "image/png")
        ]
        assert not account.boost_later.called


@pytest.mark.django_db(databases="__all__")
class TestMostReacted:
    @pytest.fixture(autouse=True)
    def setup_data(self, wrapped_cache):
        clear_wrapped_cache()
        self.me = User.register(email="react@test.com", username="reactor").identity
        self.book = Edition.objects.create(title="Reacted Book")
        self.film = Movie.objects.create(title="Reacted Film")
        self.other = Edition.objects.create(title="Other Book")

    def text(self, cls, item, when, vis=0, likes=0, boosts=0, replies=0, **fields):
        """A comment, note or review saved with its post (a comment posts through its mark otherwise), and
        the counts that post keeps."""
        piece = cls(
            owner=self.me, item=item, visibility=vis, created_time=when, **fields
        )
        piece.save(index_when_save=False, post_when_save=True)
        assert piece.latest_post_id
        TakahePost.objects.filter(pk=piece.latest_post_id).update(
            stats={"likes": likes, "boosts": boosts, "replies": replies}
        )
        return piece

    def reacted(self, level):
        return build_payload(self.me, YEAR, level, TZ)["reacted"]

    def test_likes_and_boosts_rank_and_replies_only_show(self):
        self.text(Comment, self.book, at(2, 1), text="two and two", likes=2, boosts=2)
        self.text(
            Review,
            self.film,
            at(2, 2),
            title="Talked about",
            body="many replies",
            likes=3,
            replies=9,
        )
        r = self.reacted(0)
        assert r["kind"] == "comment" and r["text"] == "two and two"
        assert (r["likes"], r["boosts"], r["replies"]) == (2, 2, 0)
        assert r["item"]["title"] == "Reacted Book"
        post = Comment.objects.get(owner=self.me, item=self.book).latest_post_id
        assert r["url"].endswith(f"/@reactor/posts/{post}/")

    def test_a_tie_goes_to_the_earlier_text(self):
        self.text(Comment, self.film, at(5, 1), text="later", likes=3)
        self.text(Note, self.book, at(4, 1), content="earlier", boosts=3)
        r = self.reacted(0)
        assert (r["kind"], r["text"]) == ("note", "earlier")

    def test_each_audience_counts_only_the_posts_it_may_see(self):
        self.text(Comment, self.book, at(2, 1), text="for everyone", likes=3)
        self.text(Comment, self.film, at(2, 2), vis=1, text="for followers", likes=10)
        self.text(Note, self.other, at(2, 3), vis=2, content="only mentioned", likes=20)
        assert self.reacted(0)["text"] == "for everyone"
        assert self.reacted(1)["text"] == "for followers"
        assert self.reacted(2)["text"] == "only mentioned"
        public = json.dumps(build_payload(self.me, YEAR, 0, TZ))
        assert "for followers" not in public and "only mentioned" not in public
        # one read for every audience, as the stored deck does, picks the same texts
        together = _reacted(self.me, (0, 1, 2), *year_range(YEAR, TZ), TZ)
        assert {lv: r["text"] for lv, r in together.items() if r} == {
            0: "for everyone",
            1: "for followers",
            2: "only mentioned",
        }

    def test_a_public_text_on_a_narrower_post_stays_off_the_public_card(self):
        piece = self.text(Comment, self.book, at(2, 1), text="narrow post", likes=9)
        self.text(Comment, self.film, at(2, 2), text="wide post", likes=3)
        TakahePost.objects.filter(pk=piece.latest_post_id).update(
            visibility=Takahe.Visibilities.followers
        )
        assert self.reacted(0)["text"] == "wide post"
        assert self.reacted(1)["text"] == "narrow post"

    def test_a_score_under_the_card_rule_still_stores_its_counts(self):
        # the page hides the card under three and its Edit tile names the score, so the data keeps it
        self.text(Comment, self.book, at(2, 1), text="two likes", likes=2)
        r = self.reacted(0)
        assert (r["text"], r["likes"], r["boosts"]) == ("two likes", 2, 0)

    def test_a_sensitive_note_stays_out(self):
        self.text(
            Note,
            self.book,
            at(2, 1),
            content="behind a warning",
            likes=9,
            sensitive=True,
        )
        self.text(Comment, self.film, at(2, 2), text="plain", likes=3)
        assert self.reacted(2)["text"] == "plain"

    def test_a_text_made_private_during_the_read_stays_out(self):
        first = self.text(Comment, self.book, at(2, 1), text="was public", likes=9)
        self.text(Comment, self.film, at(2, 2), text="still public", likes=3)
        narrowed = []

        def narrowing():
            # the owner narrows the winner after the ranking read it as public
            Comment.objects.filter(pk=first.pk).update(visibility=2, text="private")
            narrowed.append(True)
            return _ctype_categories()

        with patch("journal.wrapped.data._ctype_categories", side_effect=narrowing):
            r = _reacted(self.me, (0,), *year_range(YEAR, TZ), TZ)[0]
        assert narrowed and r is not None and r["text"] == "still public"

    def test_a_text_on_a_title_that_is_gone_is_passed_over(self):
        self.text(Comment, self.other, at(2, 1), text="gone title", likes=9)
        self.text(Comment, self.book, at(2, 2), text="kept title", likes=3)
        self.other.is_deleted = True
        self.other.save()
        assert self.reacted(0)["text"] == "kept title"

    def test_texts_that_are_all_spoiler_are_passed_over_however_many(self):
        for k in range(5):
            item = Edition.objects.create(title=f"Spoiled {k}")
            self.text(Comment, item, at(2, 1 + k), text=">!the twist!<", likes=20 - k)
        self.text(Comment, self.film, at(3, 1), text="plain words", likes=3)
        # a small stretch, so the plain text is three stretches down the ranking
        with patch("journal.wrapped.data.REACTED_BATCH", 2):
            assert self.reacted(0)["text"] == "plain words"

    def test_a_hidden_card_keeps_its_text_from_visitors(self):
        self.text(Comment, self.book, at(2, 1), text="hide me", likes=5)
        deck = make_deck(self.me, hidden={"0": {"reacted": True}})
        assert stored_payload(deck, 0)["reacted"] is None
        assert stored_payload(deck, 0, is_owner=True)["reacted"]["text"] == "hide me"
        deck.hidden = {}
        deck.shown = {"0": ["intro", "reacted"]}
        assert stored_payload(deck, 0)["reacted"]["text"] == "hide me"
        deck.shown = {"0": ["intro"]}
        assert stored_payload(deck, 0)["reacted"] is None

    def test_no_likes_or_boosts_gives_no_card_data(self):
        assert self.reacted(2) is None
        self.text(Comment, self.book, at(2, 1), text="only replies", replies=4)
        assert self.reacted(2) is None
        # a text from another year does not count
        self.text(Comment, self.film, at(2, 1, year=YEAR - 1), text="old", likes=50)
        assert self.reacted(2) is None

    def test_counts_only_and_never_who_reacted(self):
        piece = self.text(Comment, self.book, at(2, 1), text="liked")
        fans = [
            User.register(email=f"fan{k}@test.com", username=f"fanreactor{k}").identity
            for k in range(3)
        ]
        for fan in fans:
            Takahe.interact_post(piece.latest_post_id, fan.pk, "like")
        Takahe.interact_post(piece.latest_post_id, fans[0].pk, "boost")
        r = self.reacted(0)
        assert (r["likes"], r["boosts"]) == (3, 1)
        assert set(r) == {
            "kind",
            "ref",
            "text",
            "more",
            "title",
            "progress",
            "chars",
            "date",
            "likes",
            "boosts",
            "replies",
            "url",
            "item",
        }
        stored = json.dumps(snapshot(self.me, YEAR, TZ))
        assert all(f"fanreactor{k}" not in stored for k in range(3))

    def test_a_deleted_post_text_or_title_drops_out(self):
        first = self.text(Comment, self.book, at(2, 1), text="first", likes=8)
        second = self.text(Comment, self.film, at(2, 2), text="second", likes=5)
        self.text(Note, self.other, at(2, 3), content="third", likes=3)
        assert self.reacted(0)["text"] == "first"
        TakahePost.objects.filter(pk=first.latest_post_id).update(state="deleted")
        assert self.reacted(0)["text"] == "second"
        second.delete()
        assert self.reacted(0)["text"] == "third"
        self.other.is_deleted = True
        self.other.save()
        assert self.reacted(0) is None

    def test_the_stored_deck_keeps_the_counts_of_its_last_update(self):
        piece = self.text(Comment, self.book, at(2, 1), text="frozen", likes=4)
        self.text(Comment, self.film, at(2, 2), vis=1, text="followers", likes=6)
        deck = make_deck(self.me)
        assert deck.data["0"]["reacted"]["text"] == "frozen"
        assert deck.data["1"]["reacted"]["text"] == "followers"
        TakahePost.objects.filter(pk=piece.latest_post_id).update(
            stats={"likes": 40, "boosts": 0, "replies": 0}
        )
        assert stored_payload(deck, 0)["reacted"]["likes"] == 4
        refresh_deck(deck)
        assert stored_payload(deck, 0)["reacted"]["likes"] == 40

    def test_reviews_carry_the_ref_the_card_compares(self):
        review = self.text(
            Review, self.book, at(2, 1), title="Mine", body="long text", likes=3
        )
        p = build_payload(self.me, YEAR, 0, TZ)
        assert p["reviews"][0]["ref"] == p["reacted"]["ref"] == str(review.uid)

    def test_queries_do_not_grow_with_texts(self):
        start, end = year_range(YEAR, TZ)

        def run():
            with (
                CaptureQueriesContext(connection) as a,
                CaptureQueriesContext(connections["takahe"]) as b,
            ):
                _reacted(self.me, (0, 1, 2), start, end, TZ)
            assert b.captured_queries
            return len(a.captured_queries) + len(b.captured_queries)

        kinds = [
            (Comment, {"text": "c"}),
            (Note, {"content": "n"}),
            (Review, {"title": "r", "body": "r"}),
        ]

        def add(n, offset):
            for k in range(n):
                cls, fields = kinds[k % 3]
                item = Edition.objects.create(title=f"Q{offset + k}")
                self.text(cls, item, at(3, 1 + offset + k), likes=1 + k, **fields)

        def spoilers(n, offset):
            # all spoiler and ranked above every plain text, so each is read and passed over
            for k in range(n):
                item = Edition.objects.create(title=f"S{offset + k}")
                self.text(Comment, item, at(4, 1 + offset + k), text=">!x!<", likes=50)

        add(3, 0)
        spoilers(1, 0)
        run()
        small = run()
        add(9, 3)
        spoilers(8, 1)
        assert run() == small


@pytest.mark.django_db(databases="__all__")
class TestQueryCount:
    def test_queries_do_not_grow_with_titles(self):
        clear_wrapped_cache()
        me = User.register(email="q@test.com", username="qcount").identity
        pal = User.register(email="q2@test.com", username="qpal").identity
        Takahe.follow(me.pk, pal.pk, force_accept=True)

        def run():
            with (
                patch(
                    "journal.wrapped.data.instance_stats",
                    return_value={"top": None, "counts": [list(range(20))] * 3},
                ),
                CaptureQueriesContext(connection) as a,
                CaptureQueriesContext(connections["takahe"]) as b,
            ):
                build_payload(me, YEAR, 2, TZ)
            return len(a.captured_queries) + len(b.captured_queries)

        def season(title):
            # a bare season title of a show with no season_count: its title reads the show and counts its seasons
            show = TVShow.objects.create(title=f"Show of {title}")
            return TVSeason.objects.create(title="Season 1", show=show)

        # one title of each type first: the item query costs one query per type, not per title,
        # and a season's title reads its show and the show's seasons, loaded for all shows at once
        for k, item in enumerate(
            [
                Edition.objects.create(title="A0"),
                Movie.objects.create(title="A1"),
                season("A2"),
            ]
        ):
            complete(me, item, at(2, k + 1), grade=8, comment="c", tags=["t"])
            complete(pal, item, at(3, k + 1))
        run()  # warm up: content types and site config load once per process
        small = run()
        for k in range(21):
            kind = k % 3
            item = (
                Edition.objects.create(title=f"B{k}")
                if kind == 0
                else Movie.objects.create(title=f"B{k}")
                if kind == 1
                else season(f"B{k}")
            )
            complete(me, item, at(4, k + 1), grade=6, comment="c", tags=["t", "u"])
            complete(pal, item, at(5, k + 1))
        assert run() == small


@pytest.mark.django_db(databases="__all__")
class TestInstanceStats:
    def test_cached_and_queued_on_miss(self):
        clear_wrapped_cache()
        me = User.register(email="i@test.com", username="istats").identity
        complete(me, Edition.objects.create(title="Top"), at(2, 2))
        with patch("journal.wrapped.aggregates.django_rq.get_queue") as q:
            assert instance_stats(YEAR) is None
            assert q.return_value.enqueue.called
            assert instance_stats(YEAR) is None
            assert q.return_value.enqueue.call_count == 1
        stats = compute_instance_stats(YEAR)
        assert stats["top"]["title"] == "Top" and stats["counts"] == [[1], [1], [1]]
        with CaptureQueriesContext(connection) as ctx:
            assert instance_stats(YEAR) == stats
        assert len(ctx.captured_queries) == 0

    def test_job_refreshes_this_year_and_the_last(self):
        clear_wrapped_cache()
        this = datetime.date.today().year
        with patch(
            "journal.jobs.wrapped.compute_instance_stats", return_value={"counts": []}
        ) as compute:
            WrappedInstanceStats().run()
        assert [c.args[0] for c in compute.call_args_list] == [this - 1, this]

    def test_the_top_title_counts_only_public_completions(self):
        clear_wrapped_cache()
        me = User.register(email="i4@test.com", username="itop").identity
        complete(me, Edition.objects.create(title="Secret"), at(2, 2), vis=2)
        # the top title reaches every visitor, so a private completion never names it
        assert compute_instance_stats(YEAR)["top"] is None
        # with that result cached, the public payload and a signed-out visitor's page carry no top title
        assert build_payload(me, YEAR, 0, TZ)["instance_top"] is None
        make_deck(me)
        r = Client().get(reverse("journal:wrapped_page", args=["itop", YEAR]))
        assert r.status_code == 200 and page_data(r)["instance_top"] is None
        assert b"Secret" not in r.content
        opened = Edition.objects.create(title="Open")
        complete(me, opened, at(2, 3))
        assert compute_instance_stats(YEAR)["top"]["title"] == "Open"
        # nor does a deleted title
        Edition.objects.filter(pk=opened.pk).update(is_deleted=True)
        assert compute_instance_stats(YEAR)["top"] is None

    def test_a_stored_deck_never_keeps_a_cold_cache_gap(self):
        clear_wrapped_cache()
        me = User.register(email="i6@test.com", username="icold").identity
        complete(me, Edition.objects.create(title="Open"), at(2, 3))
        deck = make_deck(me)
        assert all(not deck.data[str(lv)]["warming"] for lv in (0, 1, 2))
        assert deck.data["0"]["instance_top"]["title"] == "Open"

    def test_a_deck_stored_while_another_process_computes_is_filled_later(
        self, deck_jobs
    ):
        clear_wrapped_cache()
        user = User.register(email="i7@test.com", username="ibusy")
        me = user.identity
        complete(me, Edition.objects.create(title="Open"), at(2, 3))
        # another process holds the lock and has not stored the totals yet: the deck waits, then keeps a gap
        cache.add(_instance_key(YEAR) + ":lock", 1, 600)
        with patch("journal.wrapped.aggregates.time.sleep"):
            deck = make_deck(me)
        assert deck.data["0"]["warming"] and deck.data["0"]["instance_top"] is None
        # once the totals are there, the owner's next view has a job fill the gap, and the page waits for it
        compute_instance_stats(YEAR)
        owner = Client()
        owner.force_login(user)
        deck_jobs.hold = True
        with on(datetime.date(YEAR, 12, 20)):
            r = owner.get(deck_url("ibusy"))
        assert page_data(r)["updating"] is True and len(deck_jobs.held) == 1
        assert WrappedDeck.objects.get(pk=deck.pk).data["0"]["warming"]
        deck_jobs.run()
        stored = WrappedDeck.objects.get(pk=deck.pk).data["0"]
        assert not stored["warming"] and stored["instance_top"]["title"] == "Open"
        # an update with nothing new writes no data
        assert refresh_deck(WrappedDeck.objects.get(pk=deck.pk)) is False

    def test_a_page_waits_for_a_warming_job_that_ended_before_it_read_the_state(
        self,
    ):
        clear_wrapped_cache()
        user = User.register(email="i8@test.com", username="ifast")
        complete(user.identity, Edition.objects.create(title="Open"), at(2, 3))
        cache.add(_instance_key(YEAR) + ":lock", 1, 600)
        with patch("journal.wrapped.aggregates.time.sleep"):
            deck = make_deck(user.identity)
        compute_instance_stats(YEAR)
        owner = Client()
        owner.force_login(user)
        # the job runs at once, so it has ended when the page reads the state; the page still holds the data
        # read before it, so it waits, and the new update time has it reload
        with on(datetime.date(YEAR, 12, 20)):
            data = page_data(owner.get(deck_url("ifast")))
        stored = WrappedDeck.objects.get(pk=deck.pk)
        assert data["updating"] is True and refresh_state(deck.pk) == ""
        assert data["items"] and not stored.data["0"]["warming"]
        assert stored.refreshed_time
        assert data["update_time"] != stored.refreshed_time.isoformat()

    def test_a_cold_cache_marks_the_payload_as_warming(self):
        clear_wrapped_cache()
        me = User.register(email="i5@test.com", username="iwarm").identity
        complete(me, Edition.objects.create(title="Open"), at(2, 3))
        # the owner's page does not save its card list while a job's result is missing
        with patch("journal.wrapped.data.instance_stats", return_value=None):
            assert build_payload(me, YEAR, 0, TZ)["warming"] is True
        warm = {"top": None, "counts": [[]] * 3}
        with patch("journal.wrapped.data.instance_stats", return_value=warm):
            assert build_payload(me, YEAR, 0, TZ)["warming"] is False
            # the kindred card and the flag come from one read of the cached matches
            with patch("journal.wrapped.data.kindred_matches", return_value=None):
                p = build_payload(me, YEAR, 2, TZ, include_kindred=True)
                assert p["warming"] is True and p["kindred"] is None
            with patch("journal.wrapped.data.kindred_matches", return_value={}):
                p = build_payload(me, YEAR, 2, TZ, include_kindred=True)
                assert p["warming"] is False

    def test_counts_follow_the_audience(self):
        clear_wrapped_cache()
        me = User.register(email="i2@test.com", username="iaud").identity
        pal = User.register(email="i3@test.com", username="iaud2").identity
        complete(me, Edition.objects.create(title="Open"), at(2, 2))
        complete(me, Edition.objects.create(title="Mine"), at(2, 3), vis=2)
        complete(pal, Edition.objects.create(title="Friends"), at(2, 4), vis=1)
        # each audience is compared with completions of the kind it can see
        assert compute_instance_stats(YEAR)["counts"] == [[1], [1, 1], [1, 2]]
        with patch(
            "journal.wrapped.data.instance_stats",
            return_value={"top": None, "counts": [[1] * 10, [2] * 10, [3] * 10]},
        ):
            assert build_payload(me, YEAR, 0, TZ)["median"] == 1
            assert build_payload(me, YEAR, 2, TZ)["median"] == 3

    def test_median_is_one_members_count_and_needs_ten(self):
        assert _median([3, 1, 2]) is None
        # the lower of the two middle values, so it is a count some member really has
        assert _median([1, 2, 3, 4, 5, 6, 7, 8, 9, 40]) == 5
        assert _median([5] * 9 + [100, 200]) == 5
        assert _percentile(list(range(1, 11)), 8) == 70


class TestCardText:
    """The cards are drawn in the browser; the server lists the same text for gettext."""

    def script_text(self) -> tuple[set[str], set[tuple[str, str]]]:
        root = Path(settings.BASE_DIR) / "journal/static/js/wrapped"
        js = "".join((root / f"{n}.js").read_text() for n in ("stats", "trend", "deck"))
        lit = r'"((?:[^"\\]|\\.)*)"'
        plural = set(re.findall(r"\b_n\(\s*" + lit + r"\s*,\s*" + lit, js))
        single = set(re.findall(r"(?<![\w.])(?:_|N_|WR\._)\(\s*" + lit, js))
        return single - {a for a, _ in plural}, plural

    def test_scripts_and_table_list_the_same_text(self):
        single, plural = self.script_text()
        with override(None):
            table_plural = {(f(1), f(2)) for f in PLURALS}
        assert set(TEXT) == single
        assert table_plural == plural

    def test_music_track_is_the_same_in_every_language(self):
        with override("zh-hans"):
            t = music_track("bach-bwv846")
        assert t is not None
        # one short title for every language, and only the file's place and licence for the credit
        assert (
            t["title"] == "Bach: Prelude in C major, BWV 846" and t["licence"] == "CC0"
        )
        assert set(t) == {"id", "title", "licence", "page", "mp3"}

    def test_table_only_for_other_languages(self):
        with override("en"):
            assert card_text() == {}
        with override("zh-hans"):
            text = card_text()
        with override(None):
            plural_keys = {f(1) for f in PLURALS}
        assert set(TEXT) | plural_keys == set(text)
        assert all(isinstance(text[k], list) and len(text[k]) == 2 for k in plural_keys)


@pytest.mark.django_db(databases="__all__")
class TestViews:
    @pytest.fixture(autouse=True)
    def setup_data(self, wrapped_cache):
        clear_wrapped_cache()
        self.user = User.register(email="v1@test.com", username="owner")
        self.me = self.user.identity
        self.fan = User.register(email="v2@test.com", username="fan")
        self.stranger = User.register(email="v3@test.com", username="stranger")
        Takahe.follow(self.fan.identity.pk, self.me.pk, force_accept=True)
        complete(self.me, Edition.objects.create(title="Shown Book"), at(2, 2), grade=8)
        self.owner_client = Client()
        self.owner_client.force_login(self.user)

    def page(self, client):
        return client.get(reverse("journal:wrapped_page", args=["owner", YEAR]))

    def test_owner_page_renders_the_deck_once_it_is_made(self):
        make_deck(self.me)
        r = self.page(self.owner_client)
        assert r.status_code == 200
        assert b"Shown Book" in r.content and b'id="wr-publish"' in r.content
        assert b'"lang": "en' in r.content and b'"text": {}' in r.content
        assert "year_page" not in page_data(r)
        # the Edit sheet: the audience, the cards, then the music and the card data, and Delete last
        c = r.content
        assert (
            c.index(b'id="wr-vislabel"')
            < c.index(b'id="wr-hub"')
            < c.index(b'id="wr-music"')
            < c.index(b'class="wr-update"')
            < c.index(b'id="wr-delete"')
        )
        # the year page links to the cards, under the site's own header and with no stage
        r = self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
        assert b"Open my cards" in r.content and deck_url("owner").encode() in r.content
        assert b'id="wr-deck"' not in r.content and b'id="wr-publish"' not in r.content
        assert b'class="nav-logo"' in r.content and b'id="wr-stage"' not in r.content
        assert b"Your year at a glance" in r.content

    def test_before_the_deck_is_made_the_page_shows_the_year_and_no_cards(self):
        url = reverse("journal:wrapped", args=[YEAR])
        make = reverse("journal:wrapped_generate", args=[YEAR])
        # opening the page makes nothing, and the deck can be neither saved nor shared
        with on(datetime.date(YEAR, 11, 27)):
            r = self.owner_client.get(url)
            assert self.owner_client.post(make).status_code == 302
        assert not WrappedDeck.objects.filter(owner=self.me).exists()
        data = page_data(r)
        assert data["year_page"] is True and [i["title"] for i in data["items"]] == [
            "Shown Book"
        ]
        assert data["deck_made"] is False and b"Open my cards" not in r.content
        # the cards' own data is left out, and no job is asked for the instance totals
        assert data["history"] is None and data["friends"] == []
        assert b'id="wr-deck"' not in r.content and b'id="wr-publish"' not in r.content
        assert b'id="wr-pubdlg"' not in r.content and b'id="wr-mkdlg"' not in r.content
        assert b"cards from" in r.content and make.encode() not in r.content
        assert self.page(Client()).status_code == 404
        save = reverse("journal:wrapped_save", args=[YEAR])
        body = json.dumps({"aud": 0, "shown": ["intro"]})
        assert (
            self.owner_client.post(
                save, body, content_type="application/json"
            ).status_code
            == 404
        )
        share = reverse("journal:wrapped_share", args=[YEAR])
        assert self.owner_client.post(share, {"aud": 0}).status_code == 404
        # in the window the page offers the step, and in December says the card data refreshes daily
        with on(datetime.date(YEAR, 12, 20)):
            assert b"every day in December" in self.owner_client.get(url).content
        # making the deck twice keeps one; then the cards open
        with on(datetime.date(YEAR + 1, 1, 31)):
            r = self.owner_client.get(url)
            assert make.encode() in r.content
            assert b"every day in December" not in r.content
            # the first submit queues the job and waits on the year page; the second finds the deck made
            r = self.owner_client.post(make)
            assert r.status_code == 302 and r["Location"] == url
            r = self.owner_client.post(make)
            assert r.status_code == 302 and r["Location"] == deck_url("owner")
        assert WrappedDeck.objects.filter(owner=self.me, year=YEAR).count() == 1
        assert self.page(Client()).status_code == 200
        # a year outside the supported range is refused without an error
        for bad in (1, 9999):
            r = self.owner_client.post(reverse("journal:wrapped_generate", args=[bad]))
            assert r.status_code == 302
            assert r["Location"] == reverse("journal:wrapped", args=[bad])
        # after the window a deck is no longer made, and the year page no longer offers it
        with on(datetime.date(YEAR + 1, 2, 1)):
            late = reverse("journal:wrapped_generate", args=[YEAR - 1])
            r = self.owner_client.get(reverse("journal:wrapped", args=[YEAR - 1]))
            assert r.status_code == 200 and b'id="wr-pending"' not in r.content
            assert late.encode() not in r.content
            self.owner_client.post(late)
            # a deck made in time keeps its link
            r = self.owner_client.get(url)
            assert (
                b"Open my cards" in r.content
                and deck_url("owner").encode() in r.content
            )
        assert not WrappedDeck.objects.filter(owner=self.me, year=YEAR - 1).exists()

    def test_a_visitor_is_offered_their_own_cards_only_while_they_can_be_made(self):
        make_deck(self.me)
        own = reverse("journal:wrapped", args=[YEAR])
        with on(datetime.date(YEAR + 1, 1, 31)):
            assert page_data(self.page(Client()))["make_url"] == own
        with on(datetime.date(YEAR + 1, 2, 1)):
            assert "make_url" not in page_data(self.page(Client()))
            # the owner keeps the way back to the year page
            assert page_data(self.page(self.owner_client))["make_url"] == own

    def test_share_warns_when_signed_out_visitors_cannot_open_the_page(self):
        make_deck(self.me)
        url = deck_url("owner")
        # the Link and QR code tab draws one warning for both from the page data
        assert page_data(self.owner_client.get(url))["anon_ok"] is True
        self.me.anonymous_viewable = False
        self.me.save()
        data = page_data(self.owner_client.get(url))
        assert data["anon_ok"] is False
        assert data["urls"]["preferences"] == reverse("users:preferences")
        assert self.page(Client()).status_code in (302, 403)

    def test_share_counts_empty_audiences_before_the_owner_views_them(self) -> None:
        deck = make_deck(self.me)
        for level in ("0", "1"):
            deck.data[level]["items"] = []
        deck.save(update_fields=["data"])
        audiences = page_data(self.page(self.owner_client))["audiences"]
        assert [(a["shown"], a["off"]) for a in audiences] == [
            (None, True),
            (None, True),
            (None, False),
        ]

    def test_owner_without_year_goes_to_the_year_of_the_season(self):
        url = reverse("journal:wrapped_current_year")
        # this year in December, the year before in the other months
        for day, year in (
            (datetime.date(2026, 12, 1), 2026),
            (datetime.date(2027, 2, 1), 2026),
            (datetime.date(2027, 11, 30), 2026),
        ):
            with on(day):
                r = self.owner_client.get(url)
            assert r["Location"] == reverse("journal:wrapped", args=[year])
        # the year menu lists the years with a deck and the year whose deck can be made now
        make_deck(self.me, 2023)
        with on(datetime.date(2027, 1, 5)):
            data = page_data(
                self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
            )
        assert data["years"] == [2026, 2023]

    def test_save_cleans_input_for_one_audience(self):
        url = reverse("journal:wrapped_save", args=[YEAR])
        make_deck(self.me)
        body = {
            "aud": 1,
            "order": ["shelves", "intro", "BAD ID", 3],
            "hidden": {"quiet": True, "x y": True, "*": True},
            "shown": ["intro", "shelves"],
            "made": [
                {
                    "id": 1,
                    "title": "t" * 500,
                    "n": 50,
                    "source": {"type": "auto", "cat": "nope"},
                },
                {
                    "id": 2,
                    "source": {
                        "type": "hand",
                        "items": [
                            {"url": "javascript:alert(1)"},
                            {"url": "/book/x", "title": "X"},
                        ],
                    },
                },
                {"id": 3, "source": {"type": "evil"}},
            ],
        }
        r = self.owner_client.post(
            url, json.dumps(body), content_type="application/json"
        )
        assert r.json() == {"ok": True}
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert deck.order == ["shelves", "intro"]
        # only the followers' choices change; everyone and the owner keep theirs
        assert deck.hidden == {"0": {}, "1": {"quiet": True, "*": True}, "2": {}}
        assert deck.shown["1"] == ["intro", "shelves"]
        assert (
            len(deck.made) == 2
            and deck.made[0]["n"] == 10
            and len(deck.made[0]["title"]) == 120
        )
        assert deck.made[0]["source"] == {"type": "auto", "cat": "all"}
        assert [i["url"] for i in deck.made[1]["source"]["items"]] == ["/book/x"]
        # NUL and a lone surrogate in a title are stored cleaned, not refused by the database
        r = self.owner_client.post(
            url,
            '{"aud": 0, "made": [{"id": 1, "title": "a\\u0000b\\ud800", "n": 5, "source": {"type": "auto", "cat": "all"}}]}',
            content_type="application/json",
        )
        assert r.status_code == 200
        assert (
            WrappedDeck.objects.get(owner=self.me, year=YEAR).made[0]["title"]
            == "ab\ufffd"
        )
        # json.loads reads Infinity as a float: a bad value, not a server error
        r = self.owner_client.post(
            url, '{"aud": Infinity}', content_type="application/json"
        )
        assert r.status_code == 400
        r = self.owner_client.post(
            url,
            '{"aud": 0, "made": [{"id": Infinity, "n": 5, "source": {"type": "auto", "cat": "all"}}]}',
            content_type="application/json",
        )
        assert r.status_code == 200
        assert WrappedDeck.objects.get(owner=self.me, year=YEAR).made == []
        for bad in ({}, {"aud": 7}, {"aud": "x"}):
            r = self.owner_client.post(
                url, json.dumps(bad), content_type="application/json"
            )
            assert r.status_code == 400

    def test_export_reads_the_stored_cover_file(self):
        name = default_storage.save(
            "item/book/2025/01/01/wrapped-test.png", ContentFile(PNG)
        )
        try:
            r = Client().get(reverse("journal:wrapped_cover", args=[name]))
            body = b"".join(getattr(r, "streaming_content"))
            assert r.status_code == 200 and body == PNG
            for bad in (
                "item/book/../../../x.png",
                "users/a/x.png",
                "item/book/2025/x.txt",
                "item/book/2025/01/01/missing.png",
            ):
                r = Client().get(reverse("journal:wrapped_cover", args=[bad]))
                assert r.status_code == 404
        finally:
            default_storage.delete(name)
        # the page tells the script where a media file's link starts, so it can name the file
        make_deck(self.me)
        data = page_data(self.page(self.owner_client))
        assert data["media_prefix"].endswith(settings.MEDIA_URL)
        assert data["cover_url"] == "/wrapped/cover/"

    def test_hand_picked_titles_keep_only_this_sites_covers(self):
        make_deck(self.me)
        site = settings.SITE_INFO["site_url"].rstrip("/")
        items = [
            {"url": "/book/a", "title": "A", "cover": site + "/m/item/a.jpg"},
            {"url": "/book/b", "title": "B", "cover": "https://tracker.example/p.png"},
            {"url": "/book/c", "title": "C", "cover": "//tracker.example/p.png"},
            # a link that names another host is not a title of this site
            {"url": "//tracker.example/book", "title": "D"},
            {"url": "/\\tracker.example/book", "title": "E"},
        ]
        made = [{"id": 1, "n": 3, "source": {"type": "hand", "items": items}}]
        r = self.owner_client.post(
            reverse("journal:wrapped_save", args=[YEAR]),
            json.dumps({"aud": 0, "made": made}),
            content_type="application/json",
        )
        assert r.status_code == 200
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        kept = deck.made[0]["source"]["items"]
        assert [(i["url"], i["cover"]) for i in kept] == [
            ("/book/a", site + "/m/item/a.jpg"),
            ("/book/b", ""),
            ("/book/c", ""),
        ]

    def test_music_is_saved_and_reaches_visitors(self):
        save = reverse("journal:wrapped_save", args=[YEAR])
        make_deck(self.me)
        r = self.page(self.owner_client)
        assert b'"music": null' in r.content and b"bach-bwv846" in r.content
        assert b'"music": null' in self.page(Client()).content  # no music by default
        r = self.owner_client.post(
            save,
            json.dumps({"aud": 0, "music": "nope"}),
            content_type="application/json",
        )
        assert r.status_code == 400
        r = self.owner_client.post(
            save,
            json.dumps({"aud": 0, "music": "bach-bwv846"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert WrappedDeck.objects.get(owner=self.me, year=YEAR).music == "bach-bwv846"
        r = self.page(Client())
        assert b"upload.wikimedia.org" in r.content and b"music_menu" not in r.content
        self.owner_client.post(
            save, json.dumps({"aud": 0, "music": ""}), content_type="application/json"
        )
        assert WrappedDeck.objects.get(owner=self.me, year=YEAR).music == ""

    def words_card(self, text, vis):
        book = Edition.objects.create(title="Words Book")
        c = Comment(
            owner=self.me, item=book, text=text, visibility=vis, created_time=at(3, 3)
        )
        c.save(**QUIET)
        made = [
            {
                "id": 7,
                "title": "",
                "caption": "",
                "n": 3,
                "source": {
                    "type": "words",
                    "url": book.url,
                    "kind": "comment",
                    "ref": str(c.uid),
                },
            }
        ]
        return c, made

    def test_a_words_card_reaches_only_audiences_that_may_see_its_text(self):
        c, made = self.words_card("only my followers may read this line", 1)
        make_deck(self.me, made=made)
        fan = Client()
        fan.force_login(self.fan)
        anon = self.page(Client()).content
        assert b"only my followers may read" not in anon and b"Words Book" not in anon
        assert b"only my followers may read" in self.page(fan).content
        # a saved list that names the card does not let the text out either
        WrappedDeck.objects.filter(owner=self.me).update(
            shown={"0": ["intro", "made-7"]}
        )
        assert b"only my followers may read" not in self.page(Client()).content
        # the card keeps its text until the next update
        c.text = "an edited line for followers"
        c.save(**QUIET)
        assert b"an edited line for followers" not in self.page(fan).content
        refresh_deck(WrappedDeck.objects.get(owner=self.me, year=YEAR))
        assert b"an edited line for followers" in self.page(fan).content
        r = self.owner_client.get(deck_url("owner"), {"as": 0})
        assert b"an edited line" not in r.content
        r = self.owner_client.get(deck_url("owner"), {"as": 1})
        assert b"an edited line" in r.content

    def test_words_cards_are_cleaned_and_the_picker_lists_this_year(self):
        c, made = self.words_card("x" * 700, 0)
        make_deck(self.me)
        bad = [
            {"id": 8, "source": {"type": "words", "kind": "poem", "ref": str(c.uid)}},
            {"id": 9, "source": {"type": "words", "kind": "note"}},
        ]
        r = self.owner_client.post(
            reverse("journal:wrapped_save", args=[YEAR]),
            json.dumps({"aud": 0, "made": made + bad}),
            content_type="application/json",
        )
        assert r.status_code == 200
        kept = WrappedDeck.objects.get(owner=self.me, year=YEAR).made
        assert [m["source"]["type"] for m in kept] == ["words"] and kept[0]["source"][
            "ref"
        ] == str(c.uid)
        url = reverse("journal:wrapped_words", args=[YEAR])
        assert Client().get(url).status_code == 302  # the owner only
        works = self.owner_client.get(url).json()["works"]
        text = next(t for w in works for t in w["texts"] if t["ref"] == str(c.uid))
        assert text["kind"] == "comment" and text["vis"] == 0
        assert len(text["text"]) == 600 and text["more"] and text["chars"] == 700

    def test_new_deck_starts_with_suggested_cards_up_to_the_default_visibility(self):
        make = reverse("journal:wrapped_generate", args=[YEAR])
        with on(datetime.date(YEAR, 12, 1)):
            self.owner_client.post(make)
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert deck.hidden == {"0": {}, "1": {}, "2": {}}
        assert not any(deck.is_off(lv) for lv in (0, 1, 2))
        # a member who posts to followers by default does not get a public page
        deck.delete()
        pref = self.user.preference
        pref.default_visibility = 1
        pref.save()
        with on(datetime.date(YEAR, 12, 1)):
            self.owner_client.post(make)
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert deck.is_off(0) and not deck.is_off(1) and not deck.is_off(2)

    def test_the_cards_keep_the_stored_data_and_the_year_page_is_read_now(self):
        make_deck(self.me)
        complete(self.me, Edition.objects.create(title="Late Book"), at(5, 5))
        complete(self.me, Edition.objects.create(title="Own Book"), at(6, 6), vis=2)

        def titles(r):
            return [i["title"] for i in page_data(r)["items"]]

        r = self.page(Client())
        assert titles(r) == ["Shown Book"]
        # the link preview tells what the visitor's own cards hold
        assert (
            b'property="og:title"' in r.content
            and b"finished 1 title in 2025" in r.content
        )
        assert b'property="og:url" content="http' in r.content
        assert b'property="og:image" content="http' in r.content
        # the deck page has no whole year, for visitors or the owner
        for page in (r, self.page(self.owner_client)):
            assert "live" not in page_data(page)
            assert b'id="wr-year"' not in page.content
            assert b'id="wr-piles"' not in page.content
        assert titles(self.page(self.owner_client)) == ["Shown Book"]
        assert b'property="og:title"' not in self.page(self.owner_client).content
        # the owner's year page reads the year now, with every title
        with patch("journal.views.wrapped.live_year", wraps=live_year) as read:
            r = self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
            assert read.call_args.args[2] == 2
        assert titles(r) == ["Shown Book", "Late Book", "Own Book"]
        assert b'id="wr-piles"' in r.content

    def test_update_now_until_the_end_of_january(self):
        url = reverse("journal:wrapped_refresh", args=[YEAR])
        make = reverse("journal:wrapped_generate", args=[YEAR])
        with on(datetime.date(YEAR, 12, 9)):
            # nothing to update before the deck is made
            assert self.owner_client.post(url).status_code == 404
            with patch("journal.views.wrapped.record_activity") as act:
                self.owner_client.post(make)
                complete(self.me, Edition.objects.create(title="Late Book"), at(7, 7))
                r = self.owner_client.post(url)
                assert r.status_code == 200
                assert r.json() == {"ok": True, "status": "queued"}
            assert [c.args for c in act.call_args_list] == [("annual", "web")] * 2
            deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
            titles = [i["title"] for i in deck.data["0"]["items"]]
            assert titles == ["Shown Book", "Late Book"]
            assert self.owner_client.post(url).status_code == 429  # once a minute
            r = self.page(self.owner_client)
            assert b'id="wr-update-now"' in r.content
            assert b"every day in December" in r.content
        # from 2 January only the owner updates the deck, until the end of January
        with on(datetime.date(YEAR + 1, 1, 2)):
            r = self.page(self.owner_client)
            assert b'id="wr-update-now"' in r.content
            assert b"every day in December" not in r.content
        clear_wrapped_cache()
        with on(datetime.date(YEAR + 1, 2, 1)):
            r = self.owner_client.post(url)
            assert r.status_code == 400 and r.json()["status"] == "closed"
            r = self.page(self.owner_client)
            assert (
                b'id="wr-update-now"' not in r.content and b"Updates ended" in r.content
            )

    def status(self, client=None):
        return (client or self.owner_client).get(
            reverse("journal:wrapped_status", args=[YEAR])
        )

    def test_a_job_makes_the_deck_while_the_year_page_waits(self, deck_jobs):
        make = reverse("journal:wrapped_generate", args=[YEAR])
        year_url = reverse("journal:wrapped", args=[YEAR])
        deck_jobs.hold = True
        assert self.status().json() == {
            "made": False,
            "updating": False,
            "failed": False,
            "time": None,
        }
        with on(datetime.date(YEAR, 12, 10)):
            with patch("journal.views.wrapped.record_activity") as act:
                # a second submit finds the job queued, and nothing is read in the request
                with patch("journal.wrapped.record.snapshot") as read:
                    for _ in range(2):
                        r = self.owner_client.post(make)
                        assert r.status_code == 302 and r["Location"] == year_url
                read.assert_not_called()
            assert act.call_count == 1
            assert deck_jobs.queues == ["export"] and len(deck_jobs.held) == 1
            status = self.status().json()
            assert status["updating"] and not status["made"]
            r = self.owner_client.get(year_url)
            data = page_data(r)
            assert data["making"] is True and data["deck_made"] is False
            assert data["urls"]["status"] == reverse(
                "journal:wrapped_status", args=[YEAR]
            )
            assert data["deck_url"] == deck_url("owner")
            assert b"Making your 2025 cards" in r.content
            assert make.encode() not in r.content and b"Open my cards" not in r.content
            # the deck is not there yet for visitors, nor for the owner's deck link
            assert self.page(Client()).status_code == 404
            assert self.page(self.owner_client).status_code == 302
            deck_jobs.run()
            status = self.status().json()
            assert status["made"] and not status["updating"] and status["time"]
            r = self.owner_client.get(year_url)
            assert b"Open my cards" in r.content and not page_data(r)["making"]
            assert self.page(Client()).status_code == 200
            r = self.owner_client.post(make)
            assert r["Location"] == deck_url("owner") and not deck_jobs.held
        # the status is the signed-in owner's own
        assert (
            Client().get(reverse("journal:wrapped_status", args=[YEAR])).status_code
            == 302
        )
        stranger = Client()
        stranger.force_login(self.stranger)
        assert self.status(stranger).json()["made"] is False

    def test_a_failed_job_offers_the_step_again(self, deck_jobs):
        make = reverse("journal:wrapped_generate", args=[YEAR])
        year_url = reverse("journal:wrapped", args=[YEAR])
        deck_jobs.hold = True
        with on(datetime.date(YEAR, 12, 10)):
            self.owner_client.post(make)
            with patch(
                "journal.jobs.wrapped.refresh_deck", side_effect=RuntimeError("boom")
            ):
                with pytest.raises(RuntimeError):
                    deck_jobs.run()
            status = self.status().json()
            assert status == {
                "made": False,
                "updating": False,
                "failed": True,
                "time": None,
            }
            r = self.owner_client.get(year_url)
            assert b"Your cards could not be made" in r.content
            assert make.encode() in r.content and not page_data(r)["making"]
            # a new submit queues a new job, and the failure is forgotten
            self.owner_client.post(make)
            assert len(deck_jobs.held) == 1 and self.status().json()["failed"] is False
            deck_jobs.run()
        assert WrappedDeck.objects.get(owner=self.me, year=YEAR).refreshed_time

    def test_update_now_waits_for_its_job(self, deck_jobs):
        url = reverse("journal:wrapped_refresh", args=[YEAR])
        deck = make_deck(self.me)
        before = deck.refreshed_time
        complete(self.me, Edition.objects.create(title="Late Book"), at(7, 7))
        deck_jobs.hold = True
        with on(datetime.date(YEAR, 12, 10)):
            with patch("journal.wrapped.record.snapshot") as read:
                assert self.owner_client.post(url).json() == {
                    "ok": True,
                    "status": "queued",
                }
                # while the job is queued, Update now again asks for no second one
                assert self.owner_client.post(url).json()["status"] == "queued"
                read.assert_not_called()
            assert len(deck_jobs.held) == 1 and self.status().json()["updating"]
            # the page opened meanwhile waits for the job, with the data stored before
            r = self.page(self.owner_client)
            assert page_data(r)["updating"] is True
            # the page tells a finished job from a lost one by the stored time it opened with
            assert before and page_data(r)["update_time"] == before.isoformat()
            assert page_data(r)["urls"]["status"] == reverse(
                "journal:wrapped_status", args=[YEAR]
            )
            assert "Late Book" not in [i["title"] for i in page_data(r)["items"]]
            deck_jobs.run()
            deck = WrappedDeck.objects.get(pk=deck.pk)
            after = deck.refreshed_time
            assert before and after and after > before
            assert "Late Book" in [i["title"] for i in deck.data["2"]["items"]]
            status = self.status().json()
            assert not status["updating"] and status["time"] == after.isoformat()
            assert page_data(self.page(self.owner_client))["updating"] is False
            # and then once a minute, as before
            assert self.owner_client.post(url).status_code == 429

    def test_only_the_job_that_holds_the_marker_clears_it(self, deck_jobs):
        deck = make_deck(self.me)
        deck_jobs.hold = True
        assert enqueue_deck_refresh(deck)
        func, args = deck_jobs.held.pop()
        # the first marker expires while its job waits, and a second job is queued; the first job then runs
        # and leaves the second job's marker
        cache.delete(f"wrapped:updating:{deck.pk}")
        assert enqueue_deck_refresh(deck)
        func(*args)
        assert refresh_state(deck.pk) == "updating"
        deck_jobs.run()
        assert refresh_state(deck.pk) == ""
        # a job whose marker expired while it waited holds it again for its run
        assert enqueue_deck_refresh(deck)
        cache.delete(f"wrapped:updating:{deck.pk}")
        seen = []
        real = wrapped_jobs.refresh_deck

        def watch(d):
            seen.append(refresh_state(d.pk))
            return real(d)

        with patch("journal.jobs.wrapped.refresh_deck", side_effect=watch):
            deck_jobs.run()
        assert seen == ["updating"] and refresh_state(deck.pk) == ""

    def test_a_job_that_ends_between_two_reads_is_seen_as_done(self, deck_jobs):
        url = reverse("journal:wrapped_refresh", args=[YEAR])
        deck = make_deck(self.me)
        before = deck.refreshed_time
        assert before
        deck_jobs.hold = True

        def finish_then_read(pk):
            deck_jobs.run()
            return refresh_state(pk)

        with on(datetime.date(YEAR, 12, 10)):
            # the status reads the deck, then the job stores its time and clears its marker, then the status
            # reads the state: the reply has the new time, so the page does not take the job for a lost one
            self.owner_client.post(url)
            with patch(
                "journal.views.wrapped.refresh_state", side_effect=finish_then_read
            ):
                status = self.status().json()
            after = WrappedDeck.objects.get(pk=deck.pk).refreshed_time
            assert after and after > before
            assert not status["updating"] and status["time"] == after.isoformat()
            # likewise the owner's page, which holds the data read before the job: it waits, and reloads at once
            cache.delete(f"wrapped:refresh:{deck.pk}")
            self.owner_client.post(url)
            with patch(
                "journal.views.wrapped.refresh_state", side_effect=finish_then_read
            ):
                data = page_data(self.page(self.owner_client))
            assert data["updating"] is True and data["update_time"] == after.isoformat()
            # with no job, the page does not wait
            assert page_data(self.page(self.owner_client))["updating"] is False

    def test_a_failure_belongs_to_the_job_that_holds_the_marker(self, deck_jobs):
        deck = make_deck(self.me)
        deck_jobs.hold = True
        assert enqueue_deck_refresh(deck)
        func, args = deck_jobs.held.pop()
        # the first marker expires while its job runs, a second job is queued, and then the first job fails
        cache.delete(f"wrapped:updating:{deck.pk}")
        assert enqueue_deck_refresh(deck)
        with patch("journal.jobs.wrapped.refresh_deck", side_effect=RuntimeError):
            with pytest.raises(RuntimeError):
                func(*args)
        assert refresh_state(deck.pk) == "updating"
        deck_jobs.run()
        assert refresh_state(deck.pk) == ""

    def test_a_job_leaves_a_deck_deleted_while_it_waits(self, deck_jobs):
        make = reverse("journal:wrapped_generate", args=[YEAR])
        delete = reverse("journal:wrapped_delete", args=[YEAR])
        deck_jobs.hold = True
        with on(datetime.date(YEAR, 12, 10)):
            self.owner_client.post(make)
            deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
            self.owner_client.post(delete)
            deck_jobs.run()
        assert not WrappedDeck.objects.filter(owner=self.me).exists()
        assert refresh_state(deck.pk) == ""

    def test_the_daily_update_runs_from_december_to_1_january(self):
        mine = make_deck(self.me)
        other = make_deck(self.fan.identity)
        complete(self.me, Edition.objects.create(title="Late Book"), at(8, 8))
        complete(self.fan.identity, Edition.objects.create(title="Fan Book"), at(8, 8))

        def titles(deck):
            return [
                i["title"]
                for i in WrappedDeck.objects.get(pk=deck.pk).data["2"]["items"]
            ]

        for day in (datetime.date(YEAR, 11, 30), datetime.date(YEAR + 1, 1, 2)):
            with on(day):
                WrappedDailyUpdate().run()
        assert "Late Book" not in titles(mine) and "Fan Book" not in titles(other)
        with (
            on(datetime.date(YEAR + 1, 1, 1)),
            patch("journal.jobs.wrapped.year_aggregates", wraps=year_aggregates) as agg,
        ):
            WrappedDailyUpdate().run()
        # read once for the year, not once a deck; every made deck is updated
        assert [c.args for c in agg.call_args_list] == [(YEAR,)]
        assert "Late Book" in titles(mine) and "Fan Book" in titles(other)

    def test_visitor_needs_a_deck_and_an_open_audience(self):
        assert self.page(Client()).status_code == 404  # never opened by the owner
        make_deck(self.me)
        r = self.page(Client())
        assert r.status_code == 200
        assert b"Shown Book" in r.content and b'id="wr-publish"' not in r.content
        # one link serves different cards to different viewers, so no shared cache may keep it,
        # and the browser keeps no copy to show after the viewer loses access
        assert "private" in r["Cache-Control"] and "no-store" in r["Cache-Control"]
        WrappedDeck.objects.filter(owner=self.me).update(hidden={"0": {"*": True}})
        assert self.page(Client()).status_code == 404
        # a list the owner saw empty is not shared either
        WrappedDeck.objects.filter(owner=self.me).update(hidden={}, shown={"0": []})
        assert self.page(Client()).status_code == 404

    def test_each_audience_gets_its_own_cards(self):
        complete(
            self.me, Edition.objects.create(title="Fans Only Book"), at(2, 3), vis=1
        )
        make_deck(self.me)
        fan, stranger = Client(), Client()
        fan.force_login(self.fan)
        stranger.force_login(self.stranger)
        anon = self.page(Client()).content
        assert b"Shown Book" in anon and b"Fans Only Book" not in anon
        assert b"Fans Only Book" not in self.page(stranger).content
        assert b"Fans Only Book" in self.page(fan).content
        # followers see nothing of their own: a follower gets everyone's cards
        WrappedDeck.objects.filter(owner=self.me).update(hidden={"1": {"*": True}})
        r = self.page(fan)
        assert r.status_code == 200 and b"Fans Only Book" not in r.content
        WrappedDeck.objects.filter(owner=self.me).update(
            hidden={"0": {"*": True}, "1": {"*": True}}
        )
        assert self.page(fan).status_code == 404
        assert self.page(Client()).status_code == 404

    def test_owner_views_one_audience_at_a_time(self):
        complete(
            self.me, Edition.objects.create(title="Private Note Book"), at(2, 3), vis=2
        )
        url = deck_url("owner")
        make_deck(self.me)
        r = self.owner_client.get(url)
        assert b'"level": 0' in r.content and b"Private Note Book" not in r.content
        assert b"Private Note Book" not in self.owner_client.get(url, {"as": 1}).content
        r = self.owner_client.get(url, {"as": 2})
        assert b'"level": 2' in r.content and b"Private Note Book" in r.content
        # by default the widest audience that sees something
        WrappedDeck.objects.filter(owner=self.me).update(hidden={"0": {"*": True}})
        assert b'"level": 1' in self.owner_client.get(url).content

    def test_the_owner_edits_on_the_deck_link(self):
        # before the deck is made the link sends the owner to the year page
        r = self.page(self.owner_client)
        assert r.status_code == 302 and r["Location"] == reverse(
            "journal:wrapped", args=[YEAR]
        )
        make_deck(self.me)
        r = self.page(self.owner_client)
        assert r.status_code == 200 and page_data(r)["owner_view"] is True
        assert b'id="wr-editdlg"' in r.content and b'id="wr-delete"' in r.content
        assert "private" in r["Cache-Control"] and "no-store" in r["Cache-Control"]
        # the end of the deck leads the owner back to the year page
        assert page_data(r)["make_url"] == reverse("journal:wrapped", args=[YEAR])

    def test_the_year_page_is_the_owners_alone(self):
        url = reverse("journal:wrapped", args=[YEAR])
        r = Client().get(url)
        assert r.status_code == 302 and r["Location"].startswith(reverse("users:login"))
        # another member gets their own year, never this one
        stranger = Client()
        stranger.force_login(self.stranger)
        make_deck(self.me)
        r = stranger.get(url)
        assert b"Shown Book" not in r.content and page_data(r)["deck_made"] is False
        assert deck_url("owner").encode() not in r.content

    def test_the_owner_deletes_the_deck(self):
        url = reverse("journal:wrapped_delete", args=[YEAR])
        make = reverse("journal:wrapped_generate", args=[YEAR])
        make_deck(self.me)
        make_deck(self.me, YEAR - 1)
        make_deck(self.fan.identity)
        mine = WrappedDeck.objects.filter(owner=self.me)
        # the Edit sheet tells whether new cards can be made after it
        with on(datetime.date(YEAR, 12, 10)):
            r = self.page(self.owner_client)
            assert b"You can make new cards until" in r.content
        with on(datetime.date(YEAR + 1, 2, 1)):
            r = self.page(self.owner_client)
            assert b"cards cannot be made again" in r.content
        # only a POST from the owner deletes it, and only that year's deck
        assert self.owner_client.get(url).status_code == 405
        assert Client().post(url).status_code == 302
        stranger = Client()
        stranger.force_login(self.stranger)
        stranger.post(url)
        assert mine.count() == 2
        r = self.owner_client.post(url)
        assert r.status_code == 302 and r["Location"] == reverse(
            "journal:wrapped", args=[YEAR]
        )
        assert list(mine.values_list("year", flat=True)) == [YEAR - 1]
        assert WrappedDeck.objects.filter(owner=self.fan.identity).exists()
        assert self.page(Client()).status_code == 404
        assert self.page(self.owner_client).status_code == 302
        # while the window is open, the owner can make a new deck, which starts again
        with on(datetime.date(YEAR, 12, 10)):
            r = self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
            assert make.encode() in r.content
            self.owner_client.post(make)
        deck = mine.get(year=YEAR)
        assert deck.refreshed_time is not None and deck.made == []

    def test_an_update_leaves_a_deck_deleted_meanwhile(self):
        deck = make_deck(self.me)
        real = snapshot

        def delete_while_reading(*args, **kwargs):
            WrappedDeck.objects.filter(pk=deck.pk).delete()
            return real(*args, **kwargs)

        with patch("journal.wrapped.record.snapshot", side_effect=delete_while_reading):
            assert refresh_deck(deck) is False
        assert not WrappedDeck.objects.filter(owner=self.me).exists()
        url = reverse("journal:wrapped_refresh", args=[YEAR])
        with on(datetime.date(YEAR, 12, 10)):
            assert self.owner_client.post(url).status_code == 404
            # deleted while the job of Update now reads the year: the job ends without an error
            deck = make_deck(self.me)
            with patch(
                "journal.wrapped.record.snapshot", side_effect=delete_while_reading
            ):
                assert self.owner_client.post(url).status_code == 200
        assert not WrappedDeck.objects.filter(owner=self.me).exists()
        assert refresh_state(deck.pk) == ""

    def test_the_year_page_cuts_the_year_in_the_decks_time_zone(self):
        session = self.owner_client.session
        session["detected_tz"] = "America/Los_Angeles"
        session.save()
        url = reverse("journal:wrapped", args=[YEAR])

        def zone(client):
            with patch("journal.views.wrapped.live_year", wraps=live_year) as read:
                client.get(url)
            return read.call_args.args[3]

        # the browser's zone until the deck has one of its own
        assert zone(self.owner_client) == ZoneInfo("America/Los_Angeles")
        make_deck(self.me)
        assert zone(self.owner_client) == ZoneInfo("America/Los_Angeles")
        WrappedDeck.objects.filter(owner=self.me).update(timezone="Asia/Tokyo")
        assert zone(self.owner_client) == ZoneInfo("Asia/Tokyo")

    def test_a_superuser_makes_and_updates_a_deck_at_any_time(self):
        make = reverse("journal:wrapped_generate", args=[YEAR])
        refresh = reverse("journal:wrapped_refresh", args=[YEAR])
        year_url = reverse("journal:wrapped", args=[YEAR])
        note = b"As an admin, you can make and update these cards at any time"
        # outside the window a member can neither make nor update
        with on(datetime.date(YEAR + 1, 2, 1)):
            r = self.owner_client.get(year_url)
            assert make.encode() not in r.content and note not in r.content
            self.owner_client.post(make)
        assert not WrappedDeck.objects.filter(owner=self.me).exists()
        self.user.is_superuser = True
        self.user.save()
        # before the window opens
        with on(datetime.date(YEAR, 11, 20)):
            r = self.owner_client.get(year_url)
            assert make.encode() in r.content and note in r.content
            r = self.owner_client.post(make)
            assert r.status_code == 302 and r["Location"] == year_url
        assert WrappedDeck.objects.get(owner=self.me, year=YEAR).refreshed_time
        # and after it closes
        with on(datetime.date(YEAR + 1, 2, 1)):
            r = self.page(self.owner_client)
            assert b'id="wr-update-now"' in r.content and note in r.content
            assert page_data(r)["update_open"] is True
            assert b"cards cannot be made again" not in r.content
            complete(self.me, Edition.objects.create(title="Late Book"), at(7, 7))
            assert self.owner_client.post(refresh).status_code == 200
            # never for a year still to come
            self.owner_client.post(reverse("journal:wrapped_generate", args=[YEAR + 2]))
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert "Late Book" in [i["title"] for i in deck.data["2"]["items"]]
        assert not WrappedDeck.objects.filter(owner=self.me, year=YEAR + 2).exists()

    def test_followers_page_payload_excludes_private(self):
        complete(
            self.me, Edition.objects.create(title="Private Note Book"), at(2, 3), vis=2
        )
        make_deck(self.me)
        fan = Client()
        fan.force_login(self.fan)
        assert b"Private Note Book" not in self.page(fan).content

    def test_share_rejects_non_png(self):
        make_deck(self.me)
        url = reverse("journal:wrapped_share", args=[YEAR])
        r = self.owner_client.post(
            url,
            {
                "img": base64.b64encode(b"GIF89a....").decode(),
                "visibility": 0,
                "aud": 0,
            },
        )
        assert r.status_code == 400
        r = self.owner_client.post(url, {"visibility": 0, "aud": 0})
        assert r.status_code == 400
        # a PNG header on data that does not decode is refused before any upload
        broken = base64.b64encode(PNG[:30]).decode()
        with patch("journal.views.wrapped.Takahe.upload_image") as up:
            r = self.owner_client.post(
                url,
                {
                    "img": [base64.b64encode(PNG).decode(), broken],
                    "visibility": 0,
                    "aud": 0,
                },
            )
        assert r.status_code == 400 and not up.called

    def test_share_posts_all_images(self):
        png = "data:image/png;base64," + base64.b64encode(PNG).decode()
        make_deck(self.me)
        url = reverse("journal:wrapped_share", args=[YEAR])
        with (
            patch("journal.views.wrapped.Takahe.upload_image") as up,
            patch("journal.views.wrapped.Takahe.post") as post,
        ):
            post.return_value.url = "https://example.org/p/1"
            r = self.owner_client.post(
                url,
                {"img": [png, png], "comment": "my year", "visibility": 0, "aud": 0},
                HTTP_ACCEPT="application/json",
            )
        assert r.status_code == 200 and up.call_count == 2
        assert post.call_args.kwargs["attachments"] == [
            up.return_value,
            up.return_value,
        ]

    def test_crosspost_follows_the_checkbox(self):
        make_deck(self.me)
        png = "data:image/png;base64," + base64.b64encode(PNG).decode()
        url = reverse("journal:wrapped_share", args=[YEAR])
        with (
            patch("journal.views.wrapped.Takahe.upload_image"),
            patch("journal.views.wrapped.Takahe.post") as post,
            patch("users.models.User.mastodon", new_callable=PropertyMock) as mastodon,
            patch("users.models.User.bluesky", new_callable=PropertyMock) as bluesky,
        ):
            post.return_value.url = "https://example.org/p/1"
            for flag in ("", "1"):
                r = self.owner_client.post(
                    url,
                    {"img": [png], "visibility": 0, "aud": 0, "crosspost": flag},
                    HTTP_ACCEPT="application/json",
                )
                assert r.status_code == 200
                # off: the post stays on this site; on: a public post is boosted and goes to Bluesky
                assert mastodon.return_value.boost_later.called == bool(flag)
                assert bluesky.return_value.post.called == bool(flag)
                assert not mastodon.return_value.post.called
            # the box shows only with an account it can post to, checked as the member's preference says
            page = deck_url("owner")
            box = re.compile(
                rb'<input type="checkbox"\s+id="wr-pub-xpost"\s*(checked)?'
            )
            m = box.search(self.owner_client.get(page).content)
            assert m and m.group(1)
            pref = self.user.preference
            pref.mastodon_default_repost = False
            pref.save()
            m = box.search(self.owner_client.get(page).content)
            assert m and not m.group(1)
            mastodon.return_value = bluesky.return_value = None
            assert b'id="wr-pub-xpost"' not in self.owner_client.get(page).content

    def test_share_never_reaches_wider_than_the_cards(self):
        png = "data:image/png;base64," + base64.b64encode(PNG).decode()
        make_deck(self.me)
        url = reverse("journal:wrapped_share", args=[YEAR])
        with (
            patch("journal.views.wrapped.Takahe.upload_image"),
            patch("journal.views.wrapped.Takahe.post") as post,
        ):
            post.return_value.url = "https://example.org/p/1"
            # followers' cards carry followers-only counts: never in a public post
            r = self.owner_client.post(url, {"img": [png], "visibility": 0, "aud": 1})
            assert r.status_code == 400 and not post.called
            r = self.owner_client.post(
                url,
                {"img": [png], "visibility": 1, "aud": 1},
                HTTP_ACCEPT="application/json",
            )
            assert r.status_code == 200 and post.called

    def test_share_posts_a_video_with_its_still(self):
        mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 100
        png = PNG
        make_deck(self.me)
        url = reverse("journal:wrapped_share", args=[YEAR])
        form = {"kind": "video", "comment": "my year", "visibility": 0, "aud": 0}
        with (
            patch("journal.views.wrapped.Takahe.upload_attachment") as up,
            patch("journal.views.wrapped.Takahe.post") as post,
        ):
            post.return_value.url = "https://example.org/p/1"
            r = self.owner_client.post(
                url,
                {
                    **form,
                    "video": SimpleUploadedFile("v.mp4", mp4, "video/mp4"),
                    "poster": SimpleUploadedFile("p.png", png, "image/png"),
                },
                HTTP_ACCEPT="application/json",
            )
            assert r.status_code == 200
            assert (
                up.call_args.args[3] == "video/mp4"
                and up.call_args.kwargs["poster"] == png
            )
            assert post.call_args.kwargs["attachments"] == [up.return_value]
            r = self.owner_client.post(
                url,
                {
                    **form,
                    "video": SimpleUploadedFile("v.mp4", b"not a video", "video/mp4"),
                    "poster": SimpleUploadedFile("p.png", png, "image/png"),
                },
            )
            assert r.status_code == 400
            # a still that does not decode is refused before any attachment is made
            r = self.owner_client.post(
                url,
                {
                    **form,
                    "video": SimpleUploadedFile("v.mp4", mp4, "video/mp4"),
                    "poster": SimpleUploadedFile("p.png", png[:30], "image/png"),
                },
            )
            assert r.status_code == 400 and up.call_count == 1

    def test_video_attachment_keeps_its_still(self):
        still = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        a = Takahe.upload_attachment(
            self.me.pk,
            "wrapped-test.mp4",
            File(io.BytesIO(b"\x00\x00\x00\x18ftypmp42")),
            "video/mp4",
            "alt",
            poster=still,
        )
        try:
            assert a.is_video() and (a.width, a.height) == (1, 1)
            assert a.thumbnail and a.blurhash
        finally:
            a.file.delete(save=False)  # pyrefly: ignore[missing-attribute]
            a.thumbnail.delete(save=False)  # pyrefly: ignore[missing-attribute]
            a.delete()

    def test_lookup(self):
        url = reverse("journal:wrapped_lookup")
        assert (
            self.owner_client.get(url, {"url": "not a link"}).json()["status"]
            == "invalid"
        )
        book = Edition.objects.create(title="Local One")
        r = self.owner_client.get(
            url, {"url": settings.SITE_INFO["site_url"] + book.url}
        ).json()
        assert r["status"] == "found" and r["item"]["t"] == "Local One"
        # answering never contacts another site: no fallback probe, and a GET starts no fetch
        link = "https://example.com/nothing"
        with (
            patch(
                "catalog.common.sites.SiteManager.get_fallback_class_by_url",
                side_effect=AssertionError("probed"),
            ),
            patch("journal.views.wrapped.enqueue_fetch") as enqueue,
            patch("journal.views.wrapped.fetch_job", return_value=None),
        ):
            assert (
                self.owner_client.get(url, {"url": link}).json()["status"] == "unknown"
            )
            assert not enqueue.called
            # only a POST starts a fetch, as a queued job under the fetch limits
            with patch("journal.views.wrapped.get_fetch_lock", return_value=True):
                r = self.owner_client.post(url, {"url": link})
            assert r.json()["status"] == "fetching" and enqueue.called

        class Job:
            def __init__(self, state, result, meta=None):
                self.state, self.result, self.meta = state, result, meta or {}

            def get_status(self):
                return self.state

        # polls follow the job: still running, then its result decides
        for job, status in [
            (Job("started", None), "fetching"),
            (Job("finished", "-"), "unsupported"),
            (Job("finished", book.url), "found"),
            # a job that failed (timed out, say) says nothing about the site
            (Job("failed", None), "error"),
            # the worker found a site (through a redirect, say) but could not read it
            (Job("finished", "-", {"fetch_failed": True}), "error"),
        ]:
            with patch("journal.views.wrapped.fetch_job", return_value=job):
                assert (
                    self.owner_client.get(url, {"url": link}).json()["status"] == status
                )
        # a site this instance knows that could not be read just now: a GET says the lookup failed,
        # and a POST tries again under the fetch limits
        site = MagicMock()
        site.get_item.return_value = None
        with (
            patch(
                "journal.views.wrapped.SiteManager.get_site_by_url", return_value=site
            ),
            patch("journal.views.wrapped.fetch_job", return_value=Job("finished", "-")),
            patch("journal.views.wrapped.enqueue_fetch", return_value="id") as enqueue,
            patch("journal.views.wrapped.get_fetch_lock", return_value=True),
        ):
            assert self.owner_client.get(url, {"url": link}).json()["status"] == "error"
            assert not enqueue.called
            r = self.owner_client.post(url, {"url": link})
            assert r.json()["status"] == "fetching" and enqueue.called
        # the same for a link no pattern here matches, when the worker found its site and failed to read it
        with (
            patch(
                "journal.views.wrapped.fetch_job",
                return_value=Job("finished", "-", {"fetch_failed": True}),
            ),
            patch("journal.views.wrapped.enqueue_fetch", return_value="id") as enqueue,
            patch("journal.views.wrapped.get_fetch_lock", return_value=True),
        ):
            r = self.owner_client.post(url, {"url": link})
            assert r.json()["status"] == "fetching" and enqueue.called
        # a link urlparse cannot read
        r = self.owner_client.get(url, {"url": "http://[::1/x"})
        assert r.json()["status"] == "invalid"

    def test_the_fetch_job_notes_a_failed_read_of_a_found_site(self):
        from catalog.search.utils import _fetch_task

        job = MagicMock(meta={})
        site = MagicMock()
        site.get_resource_ready.return_value = None
        with (
            patch("catalog.search.utils.get_current_job", return_value=job),
            patch(
                "catalog.search.utils.SiteManager.get_site_by_url", return_value=site
            ),
        ):
            assert _fetch_task("https://example.com/x", False, None) == "-"
        assert job.meta == {"fetch_failed": True} and job.save_meta.called
        # no site at all: unsupported, nothing to try again
        job = MagicMock(meta={})
        with (
            patch("catalog.search.utils.get_current_job", return_value=job),
            patch(
                "catalog.search.utils.SiteManager.get_site_by_url", return_value=None
            ),
            patch("catalog.search.utils.search_by_ap_url", return_value=None),
        ):
            assert _fetch_task("https://example.com/x", False, None) == "-"
        assert job.meta == {}

    def test_avatar_route_and_payload(self):
        # no picture of its own: no link in the payload, a 404 from the route, and the cards keep the site icon
        url = reverse("journal:wrapped_avatar", args=[self.me.pk])
        assert build_payload(self.me, YEAR, 0, TZ)["member"]["avatar_card"] is None
        assert Client().get(url).status_code == 404
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        ti = self.me.takahe_identity
        ti.icon.save("wrapped-test.png", ContentFile(png), save=True)  # pyrefly: ignore[missing-attribute]
        try:
            link = build_payload(self.me, YEAR, 0, TZ)["member"]["avatar_card"]
            assert link.startswith(url + "?v=")
            r = Client().get(link)
            assert r.status_code == 200 and r.getvalue() == png
            assert (
                Client().get(reverse("journal:wrapped_avatar", args=[0])).status_code
                == 404
            )
        finally:
            ti.icon.delete(save=True)  # pyrefly: ignore[missing-attribute]

    def test_cover_route_404_without_cover(self):
        book = Edition.objects.create(title="No Cover")
        assert (
            Client().get(reverse("journal:wrapped_cover", args=[book.uuid])).status_code
            == 404
        )
        assert (
            Client().get(reverse("journal:wrapped_cover", args=["nope"])).status_code
            == 404
        )
