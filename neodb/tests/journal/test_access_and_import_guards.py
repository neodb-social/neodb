import os
import zipfile
from io import BytesIO

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from catalog.common.downloaders import set_mock_mode
from catalog.models import Edition
from journal.importers import NdjsonImporter, OPMLImporter
from journal.importers.letterboxd import _is_letterboxd_url
from journal.importers.base import extract_zip_safely, sniff_media
from journal.models import Collection, FeaturedCollection, Shelf, ShelfMember, ShelfType
from journal.search import JournalIndex
from takahe.utils import Takahe
from users.models import User


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, format="PNG")
    return buf.getvalue()


def _zip(path, members: dict[str, bytes]) -> zipfile.ZipFile:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return zipfile.ZipFile(path)


@pytest.mark.django_db(databases="__all__")
class TestShelfApItemsVisibility:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.alice = User.register(email="alice@guard.test", username="guard_alice")
        self.bob = User.register(email="bob@guard.test", username="guard_bob")
        self.shelf = self.alice.identity.shelf_manager.get_shelf(ShelfType.COMPLETE)
        for i, visibility in enumerate([0, 1, 2]):
            ShelfMember.objects.create(
                parent=self.shelf,
                owner=self.alice.identity,
                item=Edition.objects.create(title=f"Book {visibility}"),
                position=i + 1,
                visibility=visibility,
            )

    def test_anonymous_items_page_lists_only_public_marks(self):
        url = reverse(
            "journal:shelf_ap_items",
            args=[self.alice.identity.handle, ShelfType.COMPLETE],
        )
        response = Client().get(url, {"page": 1})
        assert response.status_code == 200
        body = response.json()
        assert len(body["orderedItems"]) == 1
        envelope = Client().get(url).json()
        assert envelope["totalItems"] == 1

    def test_items_follow_viewer_relationship(self):
        assert self.shelf.ap_total_items() == 1
        assert len(self.shelf.ap_items_page(1)["orderedItems"]) == 1
        assert self.shelf.ap_total_items(self.bob.identity) == 1
        self.bob.identity.follow(self.alice.identity, force_accept=True)
        # the visibility ceiling is memoized per instance
        shelf = Shelf.objects.get(pk=self.shelf.pk)
        assert shelf.ap_total_items(self.bob.identity) == 2
        assert len(shelf.ap_items_page(1, self.alice.identity)["orderedItems"]) == 3

    def test_follow_lookup_runs_once_per_viewer(self, monkeypatch):
        calls = []

        def fake_is_following(identity_pk: int, target_pk: int) -> bool:
            calls.append((identity_pk, target_pk))
            return True

        monkeypatch.setattr(Takahe, "get_is_following", staticmethod(fake_is_following))
        shelf = Shelf.objects.get(pk=self.shelf.pk)
        assert len(shelf.ap_items_page(1, self.bob.identity)["orderedItems"]) == 2
        assert calls == [(self.bob.identity.pk, self.alice.identity.pk)]
        assert len(shelf.ap_items_page(1)["orderedItems"]) == 1
        assert len(shelf.ap_items_page(1, self.alice.identity)["orderedItems"]) == 3
        assert len(calls) == 1


class _FakeQuery:
    def __init__(self, collection_pk: int, viewer_pk: int | None):
        self.collection_pk = collection_pk
        self.viewer_pk = viewer_pk


class _FakeResult:
    items: list = []
    pages = 1

    def __init__(self, books: int):
        self.total = books
        self.facet_by_category = {"book": books}


@pytest.mark.django_db(databases="__all__")
class TestDynamicCollectionViewer:
    @pytest.fixture(autouse=True)
    def setup_data(self, monkeypatch):
        self.owner = User.register(email="dyn@guard.test", username="guard_dyn")
        self.collection = Collection.objects.create(
            owner=self.owner.identity, title="Dynamic", visibility=0, query="q"
        )
        self.viewers = []

        def fake_get_query(collection, viewer, **kwargs):
            self.viewers.append(viewer)
            return None

        monkeypatch.setattr(Collection, "get_query", fake_get_query)

    def test_anonymous_api_runs_query_as_anonymous(self):
        response = Client().get(f"/api/collection/{self.collection.uuid}/item/")
        assert response.status_code == 200
        assert self.viewers == [None]

    def test_owner_api_runs_query_as_owner(self):
        app = Takahe.get_or_create_app(
            "guard",
            "https://example.org",
            "https://example.org/cb",
            owner_pk=self.owner.identity.pk,
        )
        token = Takahe.refresh_token(app, self.owner.identity.pk, self.owner.pk)
        response = Client().get(
            f"/api/me/collection/{self.collection.uuid}/item/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 200
        assert [v.pk if v else None for v in self.viewers] == [self.owner.identity.pk]

    def test_viewerless_consumers_run_as_anonymous(self):
        collection = Collection.objects.get(pk=self.collection.pk)
        collection.get_summary()
        collection.ap_total_items()
        assert self.viewers == [None]

    def test_owner_tracks_private_only_results(self, monkeypatch):
        owner = self.owner.identity
        monkeypatch.setattr(
            Collection,
            "get_query_result",
            lambda c, viewer: _FakeResult(1 if viewer == owner else 0),
        )
        collection = Collection.objects.get(pk=self.collection.pk)
        assert collection.trackable is False
        assert collection.is_trackable_for(owner) is True
        assert collection.item_count_by_category["book"] == 0
        assert collection.item_count_by_category_for(owner)["book"] == 1

    def _fake_search(self, monkeypatch) -> list[tuple[int, int | None]]:
        searches: list[tuple[int, int | None]] = []

        def fake_get_query(collection, viewer, **kwargs):
            return _FakeQuery(collection.pk, viewer.pk if viewer else None)

        def fake_search(q):
            searches.append((q.collection_pk, q.viewer_pk))
            return _FakeResult(2 if q.viewer_pk else 1)

        monkeypatch.setattr(Collection, "get_query", fake_get_query)
        monkeypatch.setattr(JournalIndex.instance(), "search", fake_search)
        return searches

    def test_anonymous_page_runs_two_anonymous_searches(self, monkeypatch):
        searches = self._fake_search(monkeypatch)
        response = Client().get(self.collection.url)
        assert response.status_code == 200
        assert response.context["counts"]["book"] == 1
        assert searches == [(self.collection.pk, None)] * 2

    @pytest.mark.parametrize("featured", [False, True])
    def test_owner_page_searches_only_as_owner(self, monkeypatch, featured: bool):
        owner = self.owner.identity
        if featured:
            FeaturedCollection.objects.create(owner=owner, target=self.collection)
        searches = self._fake_search(monkeypatch)
        client = Client()
        client.force_login(self.owner, backend="mastodon.auth.OAuth2Backend")
        response = client.get(self.collection.url)
        assert response.status_code == 200
        assert response.context["counts"]["book"] == 2
        mine = [s for s in searches if s[0] == self.collection.pk]
        assert {v for _, v in mine} == {owner.pk}
        if not featured:
            assert len(mine) == 2


@pytest.mark.django_db(databases="__all__")
class TestPostInteractionVisibility:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.author = User.register(email="au@guard.test", username="guard_author")
        self.other = User.register(email="ot@guard.test", username="guard_other")
        self.client = Client()
        self.client.force_login(self.other, backend="mastodon.auth.OAuth2Backend")

    def _post(self, visibility: Takahe.Visibilities):
        post = Takahe.post(self.author.identity.pk, "hello", visibility)
        assert post is not None
        return post

    def _boost(self, post):
        return self.client.post(reverse("journal:post_boost", args=[post.pk]))

    def _like(self, post):
        return self.client.post(reverse("journal:post_like", args=[post.pk]))

    def _reply(self, post):
        return self.client.post(
            reverse("journal:post_reply", args=[post.pk]),
            {"content": "hi", "visibility": "0"},
        )

    def test_non_follower_cannot_interact_with_followers_only_post(self):
        post = self._post(Takahe.Visibilities.followers)
        assert self._boost(post).status_code == 403
        assert self._like(post).status_code == 403
        assert self._reply(post).status_code == 403
        assert not Takahe.post_boosted_by(post.pk, self.other.identity.pk)
        assert not Takahe.post_liked_by(post.pk, self.other.identity.pk)

    def test_follower_can_like_but_not_boost_followers_only_post(self):
        self.other.identity.follow(self.author.identity, force_accept=True)
        post = self._post(Takahe.Visibilities.followers)
        assert self._like(post).status_code == 200
        assert self._boost(post).status_code == 403
        assert not Takahe.post_boosted_by(post.pk, self.other.identity.pk)

    def test_public_post_can_be_boosted(self):
        post = self._post(Takahe.Visibilities.public)
        assert self._boost(post).status_code == 200
        assert Takahe.post_boosted_by(post.pk, self.other.identity.pk)


@pytest.mark.django_db(databases="__all__")
class TestNdjsonBundledFiles:
    @pytest.fixture(autouse=True)
    def setup_data(self, tmp_path):
        self.user = User.register(email="nd@guard.test", username="guard_nd")
        self.bundle = tmp_path / "bundle"
        self.bundle.mkdir()
        (self.bundle / "e.html").write_bytes(b"<html><script>alert(1)</script>")
        (self.bundle / "e.svg").write_bytes(
            b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        )
        (self.bundle / "poly.html").write_bytes(_png() + b"<script>alert(1)</script>")
        self.media = tmp_path / "media"
        self.importer = NdjsonImporter.create(user=self.user, file="x.zip")
        self.importer.temp_dir = str(self.bundle)

    def _stored(self) -> list[str]:
        return [f for _, _, files in os.walk(self.media) for f in files]

    def test_non_images_are_not_stored(self):
        with override_settings(MEDIA_ROOT=str(self.media)):
            assert self.importer._store_bundled_file("e.html") is None
            assert self.importer._store_bundled_file("e.svg") is None
            assert (
                self.importer._restore_note_attachment(
                    self.user.identity, {"file": "e.html", "mimetype": "image/png"}
                )
                is None
            )
        assert self._stored() == []

    def test_extension_comes_from_the_bytes(self):
        with override_settings(MEDIA_ROOT=str(self.media)):
            url = self.importer._store_bundled_file("poly.html")
        assert url and url.endswith(".png")
        assert [os.path.splitext(f)[1] for f in self._stored()] == [".png"]

    def test_sniff_media(self):
        assert sniff_media(_png()) == ("png", "image/png")
        assert sniff_media(b"<svg/>") is None
        assert sniff_media(b"\x00\x00\x00\x18ftypmp42" + b"\0" * 16) is None


class TestExtractZipSafely:
    def test_declared_size_over_limit(self, tmp_path):
        zf = _zip(tmp_path / "a.zip", {"a.csv": b"x" * 100})
        with pytest.raises(ValueError):
            extract_zip_safely(zf, str(tmp_path / "out"), max_size=10)
        assert not (tmp_path / "out" / "a.csv").exists()

    def test_member_count_over_limit(self, tmp_path):
        zf = _zip(tmp_path / "a.zip", {"a.csv": b"1", "b.csv": b"2"})
        with pytest.raises(ValueError):
            extract_zip_safely(zf, str(tmp_path / "out"), max_members=1)

    def test_traversal(self, tmp_path):
        zf = _zip(tmp_path / "a.zip", {"../evil.csv": b"1"})
        with pytest.raises(ValueError):
            extract_zip_safely(zf, str(tmp_path / "out"))

    def test_extracts_within_limits(self, tmp_path):
        zf = _zip(tmp_path / "a.zip", {"a.csv": b"1"})
        extract_zip_safely(zf, str(tmp_path / "out"))
        assert (tmp_path / "out" / "a.csv").read_bytes() == b"1"


@pytest.mark.django_db(databases="__all__")
class TestOpmlNeverFetches:
    def test_url_content_is_not_fetched(self, tmp_path, monkeypatch):
        calls = []

        def boom(*args, **kwargs):
            calls.append(args)
            raise AssertionError("listparser fetched a URL")

        monkeypatch.setattr("listparser.requests.get", boom)
        path = tmp_path / "subs.opml"
        path.write_text("https://169.254.169.254/latest/meta-data/")
        user = User.register(email="op@guard.test", username="guard_opml")
        task = OPMLImporter.create(user, file=str(path), mode=0, visibility=0)
        task.run()
        assert calls == []
        assert task.metadata["total"] == 0


class TestLetterboxdUrlGuard:
    @pytest.fixture(autouse=True)
    def mock_mode(self):
        set_mock_mode(True)
        yield
        set_mock_mode(False)

    @pytest.mark.parametrize(
        "url",
        [
            "https://letterboxd.com/film/x/",
            "https://boxd.it/abc",
            "http://www.letterboxd.com/user/review/x/",
        ],
    )
    def test_allowed(self, url):
        assert _is_letterboxd_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/letterboxd.com",
            "https://letterboxd.com.evil.example/",
            "https://notletterboxd.com/",
            "http://127.0.0.1/",
            "file:///etc/passwd",
            "ftp://letterboxd.com/x",
            "",
        ],
    )
    def test_refused(self, url):
        assert not _is_letterboxd_url(url)

    def test_refused_before_dns_outside_mock_mode(self, monkeypatch):
        set_mock_mode(False)
        monkeypatch.setattr(
            "journal.importers.letterboxd.is_valid_url", lambda url: False
        )
        assert not _is_letterboxd_url("https://letterboxd.com/film/x/")
