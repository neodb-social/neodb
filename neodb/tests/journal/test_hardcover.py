import csv
import io
import shutil
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from catalog.common.downloaders import set_mock_mode
from catalog.models import Edition, ExternalResource, IdType
from journal.importers import HardcoverImporter, StoryGraphImporter
from journal.importers.hardcover import (
    _credits,
    _rating_grade,
    _search_author,
    privacy_to_visibility,
)
from journal.importers.rym import update_row_in_matched_file
from journal.models import Mark, ShelfType, VisibilityType
from users.models import Task, User

CSV_PATH = "test_data/hardcover_library_export.csv"


def _make_edition_with_isbn(isbn13: str, title: str) -> Edition:
    edition = Edition.objects.create(title=title)
    ExternalResource.objects.create(
        item=edition,
        id_type=IdType.ISBN,
        id_value=isbn13,
        url=f"https://example.org/isbn/{isbn13}",
        scraped_time=timezone.now(),
        metadata={"title": title},
    )
    return edition


def _read_matched(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {row["Title"]: row for row in csv.DictReader(f)}


@pytest.fixture
def local_response():
    set_mock_mode(True)
    yield
    set_mock_mode(False)


@pytest.fixture
def no_local_index():
    # skip the catalog index so results don't depend on search availability
    with patch.object(StoryGraphImporter, "_match_via_local_index", return_value=None):
        yield


@pytest.mark.django_db(databases="__all__")
class TestHardcoverImporter:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="hctest@example.com", username="hctestuser")
        self.identity = self.user.identity
        self.brave_new_world = _make_edition_with_isbn(
            "9780060929879", "Brave New World"
        )
        self.nineteen_eighty_four = _make_edition_with_isbn("9789635043989", "1984")
        self.little_prince = _make_edition_with_isbn(
            "9780152023980", "The Little Prince"
        )
        self.fahrenheit = _make_edition_with_isbn("9781451673265", "Fahrenheit 451")

    def _create_matching_task(self, tmp_path):
        # matching writes "<file>-matched.csv" next to the input
        src = tmp_path / "hardcover_library_export.csv"
        shutil.copyfile(CSV_PATH, src)
        return HardcoverImporter.create(
            self.user, phase="matching", file=str(src), filename_hint="export.csv"
        )

    def test_validate_file(self):
        with open(CSV_PATH, "rb") as f:
            assert HardcoverImporter.validate_file(f)
        with open(CSV_PATH, "rb") as f:
            assert HardcoverImporter.validate_file(
                io.BytesIO(b"\xef\xbb\xbf" + f.read())
            )

    def test_invalid_file(self):
        with open("test_data/storygraph_library_export.csv", "rb") as f:
            assert not HardcoverImporter.validate_file(f)
        assert not HardcoverImporter.validate_file(
            io.BytesIO(b"Book Id,Title,Author\n1,Foo,Bar\n")
        )
        assert not HardcoverImporter.validate_file(io.BytesIO(b""))
        assert not HardcoverImporter.validate_file(None)

    def test_other_importers_reject_hardcover_file(self):
        with open(CSV_PATH, "rb") as f:
            assert not StoryGraphImporter.validate_file(f)

    def test_matching_phase(self, tmp_path, local_response, no_local_index):
        task = self._create_matching_task(tmp_path)
        task.run()

        assert task.metadata["phase"] == "preview"
        # 4 local by ISBN (one given as ISBN-10), 3 external (Google Books by
        # ISBN, OpenLibrary by ISBN, Google Books title search for an ASIN-only
        # row), 2 unmatched (no identifier, nothing found by title)
        assert task.metadata["matched_local"] == 4
        assert task.metadata["matched_external"] == 3
        assert task.metadata["unmatched"] == 2

        rows = _read_matched(task.local_path("matched_file"))
        assert len(rows) == 9

        row = rows["Brave New World"]
        assert row["link"] == self.brave_new_world.url
        assert row["match_source"] == "local"
        assert row["shelf"] == ShelfType.COMPLETE
        assert row["collect_date"] == "2022-06-15"

        # in progress: dated when reading started, not when added
        row = rows["1984"]
        assert row["link"] == self.nineteen_eighty_four.url
        assert row["shelf"] == ShelfType.PROGRESS
        assert row["collect_date"] == "2022-04-02"

        # "Stopped" is Hardcover's did-not-finish; no start date, so date added
        row = rows["The Little Prince"]
        assert row["shelf"] == ShelfType.DROPPED
        assert row["collect_date"] == "2026-04-02"

        row = rows["Fahrenheit 451"]
        assert row["link"] == self.fahrenheit.url
        assert row["shelf"] == ShelfType.WISHLIST

        # ASIN only, so found by title; the illustrator credited first is
        # not searched as the author
        row = rows["Tales from Earthsea"]
        assert row["link"] == "https://books.google.com/books?id=earthsea_test_id"
        assert row["match_source"] == "googlebooks"

        row = rows["The Hobbit"]
        assert row["link"] == "https://books.google.com/books?id=hobbit_test_id"
        assert row["match_source"] == "googlebooks"

        row = rows["Fantastic Mr Fox"]
        assert row["link"] == "https://openlibrary.org/books/OL7353617M"
        assert row["match_source"] == "openlibrary"
        assert row["shelf"] == ShelfType.PROGRESS  # Paused

        assert rows["Unknown Book"]["link"] == ""
        assert rows["Unknown Book"]["match_source"] == "none"
        assert rows["Never Opened"]["shelf"] == ""  # Ignored

    def test_import_phase(self, tmp_path, local_response, no_local_index):
        task = self._create_matching_task(tmp_path)
        task.run()
        assert task.metadata["phase"] == "preview"

        task.metadata["phase"] = "importing"
        task.save(update_fields=["metadata"])
        task.run()

        assert task.metadata["phase"] == "done"
        # the unmatched rows have no link
        assert task.metadata["imported"] == 7
        assert task.metadata["skipped"] == 2
        assert task.metadata["failed"] == 0

        mark = Mark(self.identity, self.brave_new_world)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 8
        assert mark.visibility == VisibilityType.Public
        assert mark.created_time
        assert timezone.localtime(mark.created_time).date().isoformat() == "2022-06-15"

        mark = Mark(self.identity, self.nineteen_eighty_four)
        assert mark.shelf_type == ShelfType.PROGRESS
        assert mark.rating_grade is None
        assert mark.visibility == VisibilityType.Follower_Only

        mark = Mark(self.identity, self.little_prince)
        assert mark.shelf_type == ShelfType.DROPPED
        assert mark.visibility == VisibilityType.Private

        mark = Mark(self.identity, self.fahrenheit)
        assert mark.shelf_type == ShelfType.WISHLIST

        earthsea = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780152047641",
        ).first()
        assert earthsea is not None
        mark = Mark(self.identity, earthsea)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 7
        assert mark.comment_text == "A great short story collection"

        hobbit = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780547928227",
        ).first()
        assert hobbit is not None
        assert Mark(self.identity, hobbit).rating_grade == 9

        fox = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780140328721",
        ).first()
        assert fox is not None
        assert Mark(self.identity, fox).shelf_type == ShelfType.PROGRESS

    def test_reimport_skips_unchanged(self, tmp_path, local_response, no_local_index):
        task = self._create_matching_task(tmp_path)
        task.run()
        task.metadata["phase"] = "importing"
        task.save(update_fields=["metadata"])
        task.run()
        task.metadata.update(
            phase="importing", processed=0, imported=0, skipped=0, failed=0
        )
        task.save(update_fields=["metadata"])
        task.run()
        assert task.metadata["imported"] == 0
        assert task.metadata["skipped"] == 9

    def test_reimport_applies_privacy_and_rating(
        self, tmp_path, local_response, no_local_index
    ):
        task = self._create_matching_task(tmp_path)
        task.run()
        task.metadata["phase"] = "importing"
        task.save(update_fields=["metadata"])
        task.run()

        matched = task.metadata["matched_file"]
        update_row_in_matched_file(matched, 0, {"Privacy": "Private", "Rating": "5"})
        update_row_in_matched_file(matched, 4, {"Privacy": "Followers"})
        task.metadata.update(
            phase="importing", processed=0, imported=0, skipped=0, failed=0
        )
        task.save(update_fields=["metadata"])
        task.run()
        assert task.metadata["imported"] == 2
        assert task.metadata["skipped"] == 7

        mark = Mark(self.identity, self.brave_new_world)
        assert mark.visibility == VisibilityType.Private
        assert mark.rating_grade == 10
        earthsea = Edition.objects.get(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780152047641",
        )
        mark = Mark(self.identity, earthsea)
        assert mark.visibility == VisibilityType.Follower_Only
        assert mark.comment is not None
        assert mark.comment.visibility == VisibilityType.Follower_Only

    def test_match_prefers_any_local_identifier(self):
        # the ISBN is unknown locally, but the ASIN is not
        audiobook = Edition.objects.create(title="Sample Audiobook")
        ExternalResource.objects.create(
            item=audiobook,
            id_type=IdType.ASIN,
            id_value="B0SAMPLE01",
            url="https://example.org/asin/B0SAMPLE01",
            scraped_time=timezone.now(),
            metadata={"title": "Sample Audiobook"},
        )
        row = {"Title": "Sample Audiobook", "ISBN 13": "9780000000019"}
        row["ASIN"] = "B0SAMPLE01"
        assert HardcoverImporter._match(row) == (audiobook.url, "local")

    def test_match_missing(self):
        assert HardcoverImporter._match({}) is None
        assert HardcoverImporter._match({"ISBN 13": "notanisbn"}) is None


def test_credits():
    assert _credits("Kim Writer, Lee Artist (Illustrator) , Max Editor (Editor)") == [
        ("Kim Writer", ""),
        ("Lee Artist", "Illustrator"),
        ("Max Editor", "Editor"),
    ]
    assert _credits(" , ") == []
    # an unclosed parenthesis is part of the name
    assert _credits("Kim (Writer") == [("Kim (Writer", "")]


@pytest.mark.parametrize(
    "credits,expected",
    [
        ("Kim Writer", "Kim Writer"),
        ("Kim Writer, Pat Writer", "Kim Writer"),
        ("Lee Artist (Illustrator), Kim Writer", "Kim Writer"),
        ("Max Editor (Editor), Kim Writer (Author)", "Kim Writer"),
        ("Max Editor (Editor), Lee Artist (Illustrator)", "Max Editor"),
        ("", ""),
    ],
)
def test_search_author(credits, expected):
    assert _search_author(credits) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4.5", 9),
        ("3", 6),
        ("0.5", 1),
        ("0", None),
        ("", None),
        (None, None),
        ("n/a", None),
        ("inf", None),
    ],
)
def test_rating_grade(raw, expected):
    assert _rating_grade(raw) == expected


@pytest.mark.parametrize(
    "privacy,expected",
    [
        ("Public", VisibilityType.Public),
        ("public", VisibilityType.Public),
        ("Followers", VisibilityType.Follower_Only),
        ("Followers Only", VisibilityType.Follower_Only),
        ("Private", VisibilityType.Private),
        ("", VisibilityType.Private),
        (None, VisibilityType.Private),
        ("Friends", VisibilityType.Private),
    ],
)
def test_privacy_to_visibility(privacy, expected):
    assert privacy_to_visibility(privacy) == expected


@pytest.mark.django_db(databases="__all__")
class TestHardcoverImportViews:
    @pytest.fixture(autouse=True)
    def setup_user(self, client, tmp_path, settings, monkeypatch):
        settings.MEDIA_ROOT = str(tmp_path)
        self.enqueued = []
        monkeypatch.setattr(
            HardcoverImporter, "enqueue", lambda task: self.enqueued.append(task.pk)
        )
        self.user = User.register(email="hc_view@test.com", username="hc_viewer")
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")

    def _upload(self, client, content: bytes):
        upload = SimpleUploadedFile("hardcover.csv", content, "text/csv")
        return client.post(reverse("users:import_hardcover"), {"file": upload})

    def test_data_page_shows_section(self, client):
        response = client.get(reverse("users:data"))
        assert response.status_code == 200
        assert reverse("users:import_hardcover") in response.content.decode()

    def test_upload_starts_matching(self, client):
        with open(CSV_PATH, "rb") as f:
            response = self._upload(client, f.read())
        assert response.status_code == 302
        task = HardcoverImporter.latest_task(self.user)
        assert task is not None
        assert task.metadata["phase"] == "matching"
        assert self.enqueued == [task.pk]

    def test_invalid_upload_rejected(self, client):
        response = self._upload(client, b"Book Id,Title\n1,Foo\n")
        assert response.status_code == 400
        assert HardcoverImporter.latest_task(self.user) is None

    def test_preview_edit_confirm(self, client, local_response, no_local_index):
        with open(CSV_PATH, "rb") as f:
            self._upload(client, f.read())
        task = HardcoverImporter.latest_task(self.user)
        assert task is not None
        task.run()
        task.state = Task.States.complete
        task.save(update_fields=["state"])

        response = client.get(
            reverse("users:user_task_status", args=(task.type,)),
            headers={"HX-Request": "true"},
        )
        assert response["HX-Retarget"] == "#hardcover"
        assert reverse("users:hardcover_preview") in response.content.decode()

        response = client.get(reverse("users:hardcover_preview"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Brave New World" in content
        assert "Followers Only" in content
        assert 'name="visibility"' not in content

        # row 5 is Unknown Book; give it a link to a local book
        local_url = Edition.objects.create(title="Unknown Book").url
        response = client.post(
            reverse("users:hardcover_save_row", args=(5,)),
            {"link": local_url, "shelf": "complete", "collect_date": "2024-01-02"},
        )
        assert response.status_code == 200
        assert local_url in response.content.decode()
        task.refresh_from_db()
        rows = _read_matched(task.local_path("matched_file"))
        assert rows["Unknown Book"]["link"] == local_url

        response = client.post(reverse("users:hardcover_confirm"))
        assert response.status_code == 302
        task.refresh_from_db()
        assert task.metadata["phase"] == "importing"
        assert task.state == Task.States.pending
        assert self.enqueued == [task.pk, task.pk]

    def test_reupload_matched_file_skips_matching(
        self, client, local_response, no_local_index
    ):
        with open(CSV_PATH, "rb") as f:
            self._upload(client, f.read())
        task = HardcoverImporter.latest_task(self.user)
        assert task is not None
        task.run()
        with open(task.local_path("matched_file"), "rb") as f:
            matched = f.read()

        response = self._upload(client, matched)
        assert response.status_code == 302
        task = HardcoverImporter.latest_task(self.user)
        assert task is not None
        assert task.metadata["phase"] == "preview"
        assert task.metadata["had_link_column"]
        assert len(self.enqueued) == 1

    def test_preview_tolerates_short_rows(self, client):
        # a matched file edited by hand may lose trailing cells
        self._upload(
            client,
            b"Title,Author,Status,Hardcover Book ID,ISBN 13,Privacy,link,shelf\n"
            b"Short Row,Someone\n",
        )
        response = client.get(reverse("users:hardcover_preview"))
        assert response.status_code == 200
        assert "Short Row" in response.content.decode()

    def test_preview_pages(self, client, tmp_path):
        matched = tmp_path / "paged-matched.csv"
        with open(matched, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Title", "Privacy", "link", "shelf"])
            writer.writerows([[f"Paged {n:03}", "Public", "", ""] for n in range(120)])
        HardcoverImporter.create(
            self.user, phase="preview", file="unused", matched_file=str(matched)
        )
        url = reverse("users:hardcover_preview")

        first = client.get(url).content.decode()
        assert "Paged 099" in first and "Paged 100" not in first
        assert 'href="?page=2"' in first

        # a page beyond the end shows the last one
        last = client.get(url, {"page": 7}).content.decode()
        assert "Paged 119" in last and "Paged 099" not in last
        assert 'aria-current="page">2</span>' in last

    def test_cancel(self, client):
        with open(CSV_PATH, "rb") as f:
            self._upload(client, f.read())
        response = client.post(reverse("users:hardcover_cancel"))
        assert response.status_code == 302
        task = HardcoverImporter.latest_task(self.user)
        assert task is not None
        assert task.metadata["phase"] == "cancelled"
        assert task.state == Task.States.failed
