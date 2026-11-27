import base64
import datetime
import json
import re
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connection, connections
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.translation import override

from catalog.models import Edition, Movie
from journal.jobs.wrapped import WrappedInstanceStats
from journal.models import (
    Comment,
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
    build_payload,
    compute_instance_stats,
    friends_allowed,
    instance_stats,
    plain_excerpt,
)
from journal.wrapped_text import PLURALS, TEXT, card_text
from takahe.utils import Takahe
from users.models import User

TZ = ZoneInfo("Asia/Shanghai")
QUIET = {"post_when_save": False, "index_when_save": False}
YEAR = 2025


def clear_wrapped_cache() -> None:
    # tests share redis with the dev cluster, so only this feature's keys are cleared
    cache.delete_many(
        [f"wrapped:instance:{y}{s}" for y in range(2020, 2031) for s in ("", ":lock")]
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

    def test_only_public_titles_of_viewable_members(self):
        friends = build_payload(self.me, YEAR, 0, TZ)["friends"]
        assert [f["handle"].split("@")[0] for f in friends] == ["fpal"]
        assert friends[0]["n"] == 2
        assert [i["u"] for i in friends[0]["items"]] == [self.shared.url]

    def test_visitors_get_friends_only_after_a_social_card_was_shown(self):
        deck = WrappedDeck.objects.create(owner=self.me, year=YEAR, visibility=0)
        assert friends_allowed(deck, True)
        assert not friends_allowed(deck, False)
        deck.shown = ["intro", "shelves"]
        assert not friends_allowed(deck, False)
        deck.shown = ["intro", "twin"]
        assert friends_allowed(deck, False)
        assert (
            build_payload(self.me, YEAR, 0, TZ, include_friends=False)["friends"] == []
        )


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

    def test_owner_without_year_goes_to_this_year(self):
        r = self.owner_client.get(reverse("journal:wrapped_current_year"))
        assert r.status_code == 302 and str(datetime.date.today().year) in r["Location"]

    def test_save_cleans_input_and_asks_reload_on_visibility(self):
        url = reverse("journal:wrapped_save", args=[YEAR])
        self.owner_client.get(reverse("journal:wrapped", args=[YEAR]))
        body = {
            "vis": "followers",
            "order": ["shelves", "intro", "BAD ID", 3],
            "hidden": {"quiet": True, "x y": True},
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
        assert r.json() == {"ok": True, "reload": True}
        deck = WrappedDeck.objects.get(owner=self.me, year=YEAR)
        assert (
            deck.level == 1
            and deck.order == ["shelves", "intro"]
            and deck.hidden == {"quiet": True}
        )
        assert deck.shown == ["intro", "shelves"]
        assert (
            len(deck.made) == 2
            and deck.made[0]["n"] == 10
            and len(deck.made[0]["title"]) == 120
        )
        assert deck.made[0]["source"] == {"type": "auto", "cat": "all"}
        assert [i["url"] for i in deck.made[1]["source"]["items"]] == ["/book/x"]
        r = self.owner_client.post(
            url, json.dumps({"vis": "followers"}), content_type="application/json"
        )
        assert r.json()["reload"] is False

    def test_visitor_needs_a_shared_deck(self):
        assert self.page(Client()).status_code == 404
        WrappedDeck.objects.create(owner=self.me, year=YEAR, visibility=0)
        assert self.page(Client()).status_code == 404  # never opened by the owner
        WrappedDeck.objects.filter(owner=self.me).update(shown=["intro", "shelves"])
        r = self.page(Client())
        assert r.status_code == 200
        assert b"Shown Book" in r.content and b'id="wr-publish"' not in r.content

    def test_followers_only_and_private_pages(self):
        WrappedDeck.objects.create(
            owner=self.me, year=YEAR, visibility=1, shown=["intro"]
        )
        fan, stranger = Client(), Client()
        fan.force_login(self.fan)
        stranger.force_login(self.stranger)
        assert self.page(fan).status_code == 200
        assert self.page(stranger).status_code == 404
        assert self.page(Client()).status_code == 404
        WrappedDeck.objects.filter(owner=self.me).update(visibility=2)
        assert self.page(fan).status_code == 404

    def test_owner_visiting_own_page_goes_to_the_editor(self):
        WrappedDeck.objects.create(
            owner=self.me, year=YEAR, visibility=0, shown=["intro"]
        )
        r = self.page(self.owner_client)
        assert r.status_code == 302 and r["Location"] == reverse(
            "journal:wrapped", args=[YEAR]
        )

    def test_followers_page_payload_excludes_private(self):
        complete(
            self.me, Edition.objects.create(title="Private Note Book"), at(2, 3), vis=2
        )
        WrappedDeck.objects.create(
            owner=self.me, year=YEAR, visibility=1, shown=["intro"]
        )
        fan = Client()
        fan.force_login(self.fan)
        assert b"Private Note Book" not in self.page(fan).content

    def test_share_rejects_non_png(self):
        url = reverse("journal:wrapped_share", args=[YEAR])
        r = self.owner_client.post(
            url, {"img": base64.b64encode(b"GIF89a....").decode(), "visibility": 0}
        )
        assert r.status_code == 400
        r = self.owner_client.post(url, {"visibility": 0})
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
                {"img": [png, png], "comment": "my year", "visibility": 0},
                HTTP_ACCEPT="application/json",
            )
        assert r.status_code == 200 and up.call_count == 2
        assert post.call_args.kwargs["attachments"] == [
            up.return_value,
            up.return_value,
        ]

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
        assert (
            self.owner_client.get(url, {"url": "https://example.com/nothing"}).json()[
                "status"
            ]
            == "unsupported"
        )

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
