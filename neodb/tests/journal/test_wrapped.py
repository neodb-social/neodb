import base64
import datetime
import io
import json
import re
from pathlib import Path
from unittest.mock import PropertyMock, patch
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from catalog.models import Edition, Movie
from journal.jobs.wrapped import WrappedInstanceStats
from journal.models import (
    Collection,
    Comment,
    Like,
    Rating,
    Review,
    ShelfLogEntry,
    ShelfMember,
    ShelfType,
    Tag,
    TagMember,
    WrappedDeck,
)
from journal.wrapped import (
    BULK_DAY,
    _median,
    _percentile,
    build_payload,
    compute_instance_stats,
    compute_kindred,
    friends_allowed,
    kindred_allowed,
    instance_stats,
    plain_excerpt,
    resolve_words,
)
from journal.models.wrapped import MADE_CARDS_MAX
from journal.wrapped_music import track as music_track
from journal.wrapped_text import PLURALS, TEXT, card_text
from takahe.models import Domain
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import APIdentity, User

TZ = ZoneInfo("Asia/Shanghai")
QUIET = {"post_when_save": False, "index_when_save": False}
YEAR = 2025


def clear_wrapped_cache() -> None:
    # tests share redis with the dev cluster, so only this feature's keys are cleared
    cache.delete_many(
        [
            f"wrapped:{kind}:{y}{s}"
            for kind in ("instance", "kindred")
            for y in range(2020, 2031)
            for s in ("", ":lock")
        ]
    )


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
    def setup_data(self):
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
        assert it["tags"] == [["comfort", 0]]
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


@pytest.mark.django_db(databases="__all__")
class TestFriends:
    @pytest.fixture(autouse=True)
    def setup_data(self):
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
    def setup_data(self):
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
        # a signed-out visitor's page carries no match even when the matches are ready
        compute_kindred(YEAR)
        deck.hidden, deck.shown = {}, {}
        deck.save()
        r = Client().get(reverse("journal:wrapped_page", args=["kme", YEAR]))
        assert r.status_code == 200 and b"krare" not in r.content

    def test_turning_the_card_on_for_everyone_reaches_signed_out_visitors(self):
        compute_kindred(YEAR)
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


@pytest.mark.django_db(databases="__all__")
class TestReviewFixes:
    """What the branch review found: the visitor payload, saves, and the data the cards draw from."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
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
        deck = WrappedDeck.objects.create(
            owner=self.me,
            year=YEAR,
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

    def test_saves_reject_more_made_cards_than_allowed(self):
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
        c = Client()
        c.force_login(user)
        assert page_data(c.get(reverse("journal:wrapped", args=[YEAR])))["level"] == 1
        empty = page_data(c.get(reverse("journal:wrapped", args=[YEAR]) + "?as=0"))
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
        mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 100
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
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
class TestQueryCount:
    def test_queries_do_not_grow_with_titles(self):
        clear_wrapped_cache()
        me = User.register(email="q@test.com", username="qcount").identity
        pal = User.register(email="q2@test.com", username="qpal").identity
        Takahe.follow(me.pk, pal.pk, force_accept=True)

        def run():
            with (
                patch(
                    "journal.wrapped.instance_stats",
                    return_value={"top": None, "counts": list(range(20))},
                ),
                CaptureQueriesContext(connection) as a,
                CaptureQueriesContext(connections["takahe"]) as b,
            ):
                build_payload(me, YEAR, 2, TZ)
            return len(a.captured_queries) + len(b.captured_queries)

        # one title of each type first: the item query costs one query per type, not per title
        for k, item in enumerate(
            [Edition.objects.create(title="A0"), Movie.objects.create(title="A1")]
        ):
            complete(me, item, at(2, k + 1), grade=8, comment="c", tags=["t"])
            complete(pal, item, at(3, k + 1))
        run()  # warm up: content types and site config load once per process
        small = run()
        for k in range(20):
            item = (
                Movie.objects.create(title=f"B{k}")
                if k % 2
                else Edition.objects.create(title=f"B{k}")
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
        with patch("journal.wrapped.django_rq.get_queue") as q:
            assert instance_stats(YEAR) is None
            assert q.return_value.enqueue.called
            assert instance_stats(YEAR) is None
            assert q.return_value.enqueue.call_count == 1
        stats = compute_instance_stats(YEAR)
        assert stats["top"]["title"] == "Top" and stats["counts"] == [1]
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
    def setup_data(self):
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

    def test_owner_page_creates_deck_and_renders_payload(self):
        r = self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
        assert r.status_code == 200
        assert b"Shown Book" in r.content and b'id="wr-publish"' in r.content
        assert b'"lang": "en' in r.content and b'"text": {}' in r.content
        assert WrappedDeck.objects.filter(owner=self.me, year=YEAR).exists()

    def test_share_warns_when_signed_out_visitors_cannot_open_the_page(self):
        url = reverse("journal:wrapped", args=[YEAR])
        # the QR code tab has an empty warning of its own, so the test looks for the text
        warning = b"your profile is hidden from them"
        assert warning not in self.owner_client.get(url).content
        self.me.anonymous_viewable = False
        self.me.save()
        r = self.owner_client.get(url)
        assert warning in r.content
        assert reverse("users:preferences").encode() in r.content
        assert self.page(Client()).status_code in (302, 403)

    def test_owner_without_year_goes_to_this_year(self):
        r = self.owner_client.get(reverse("journal:wrapped_current_year"))
        assert r.status_code == 302 and str(datetime.date.today().year) in r["Location"]

    def test_save_cleans_input_for_one_audience(self):
        url = reverse("journal:wrapped_save", args=[YEAR])
        self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
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
        for bad in ({}, {"aud": 7}, {"aud": "x"}):
            r = self.owner_client.post(
                url, json.dumps(bad), content_type="application/json"
            )
            assert r.status_code == 400

    def test_music_is_saved_and_reaches_visitors(self):
        save = reverse("journal:wrapped_save", args=[YEAR])
        r = self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
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
        WrappedDeck.objects.create(owner=self.me, year=YEAR, made=made)
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
        # the card follows edits of its text
        c.text = "an edited line for followers"
        c.save(**QUIET)
        assert b"an edited line for followers" in self.page(fan).content
        r = self.owner_client.get(reverse("journal:wrapped", args=[YEAR]), {"as": 0})
        assert b"an edited line" not in r.content
        r = self.owner_client.get(reverse("journal:wrapped", args=[YEAR]), {"as": 1})
        assert b"an edited line" in r.content

    def test_words_cards_are_cleaned_and_the_picker_lists_this_year(self):
        c, made = self.words_card("x" * 700, 0)
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
        self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert deck.hidden == {"0": {}, "1": {}, "2": {}}
        assert not any(deck.is_off(lv) for lv in (0, 1, 2))
        # a member who posts to followers by default does not get a public page
        deck.delete()
        pref = self.user.preference
        pref.default_visibility = 1
        pref.save()
        self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert deck.is_off(0) and not deck.is_off(1) and not deck.is_off(2)

    def test_visitor_needs_a_deck_and_an_open_audience(self):
        assert self.page(Client()).status_code == 404  # never opened by the owner
        WrappedDeck.objects.create(owner=self.me, year=YEAR)
        r = self.page(Client())
        assert r.status_code == 200
        assert b"Shown Book" in r.content and b'id="wr-publish"' not in r.content
        # one link serves different cards to different viewers, so no shared cache may keep it
        assert "private" in r["Cache-Control"]
        WrappedDeck.objects.filter(owner=self.me).update(hidden={"0": {"*": True}})
        assert self.page(Client()).status_code == 404
        # a list the owner saw empty is not shared either
        WrappedDeck.objects.filter(owner=self.me).update(hidden={}, shown={"0": []})
        assert self.page(Client()).status_code == 404

    def test_each_audience_gets_its_own_cards(self):
        complete(
            self.me, Edition.objects.create(title="Fans Only Book"), at(2, 3), vis=1
        )
        WrappedDeck.objects.create(owner=self.me, year=YEAR)
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
        url = reverse("journal:wrapped", args=[YEAR])
        r = self.owner_client.get(url)
        assert b'"level": 0' in r.content and b"Private Note Book" not in r.content
        assert b"Private Note Book" not in self.owner_client.get(url, {"as": 1}).content
        r = self.owner_client.get(url, {"as": 2})
        assert b'"level": 2' in r.content and b"Private Note Book" in r.content
        # by default the widest audience that sees something
        WrappedDeck.objects.filter(owner=self.me).update(hidden={"0": {"*": True}})
        assert b'"level": 1' in self.owner_client.get(url).content

    def test_owner_visiting_own_page_goes_to_the_editor(self):
        WrappedDeck.objects.create(owner=self.me, year=YEAR)
        r = self.page(self.owner_client)
        assert r.status_code == 302 and r["Location"] == reverse(
            "journal:wrapped", args=[YEAR]
        )

    def test_followers_page_payload_excludes_private(self):
        complete(
            self.me, Edition.objects.create(title="Private Note Book"), at(2, 3), vis=2
        )
        WrappedDeck.objects.create(owner=self.me, year=YEAR)
        fan = Client()
        fan.force_login(self.fan)
        assert b"Private Note Book" not in self.page(fan).content

    def test_share_rejects_non_png(self):
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

    def test_share_posts_all_images(self):
        png = (
            "data:image/png;base64,"
            + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()
        )
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

    def test_share_never_reaches_wider_than_the_cards(self):
        png = (
            "data:image/png;base64,"
            + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()
        )
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
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
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
            def __init__(self, state, result):
                self.state, self.result = state, result

            def get_status(self):
                return self.state

        # polls follow the job: still running, then its result decides
        for job, status in [
            (Job("started", None), "fetching"),
            (Job("finished", "-"), "unsupported"),
            (Job("finished", book.url), "found"),
            (Job("failed", None), "unsupported"),
        ]:
            with patch("journal.views.wrapped.fetch_job", return_value=job):
                assert (
                    self.owner_client.get(url, {"url": link}).json()["status"] == status
                )

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
