import pytest
from django.test import Client, override_settings
from django.urls import reverse

from journal.exporters import DoufenExporter, NdjsonExporter
from journal.importers import RymImporter, StoryGraphImporter
from users.models import Task, User

pytestmark = pytest.mark.django_db(databases="__all__")


def _member(username: str) -> tuple[User, Client]:
    user = User.register(email=f"{username}@example.com", username=username)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, client


def _completed_export(user: User, path: str) -> NdjsonExporter:
    task = NdjsonExporter.create(user=user)
    task.metadata["file"] = path
    task.state = Task.States.complete
    task.save()
    return task


def _read(response) -> bytes:
    return b"".join(response.streaming_content)


def test_export_download_streams_the_generated_file(tmp_path):
    """The artifact lives on the volume, not in the media storage.

    It used to be handed to nginx as ``X-Accel-Redirect: MEDIA_URL + relpath``,
    which names an unreachable absolute URL once MEDIA_URL is an S3 bucket.
    """
    user, client = _member("exporter")
    archive = tmp_path / "export.zip"
    archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    task = _completed_export(user, str(archive))

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        response = client.get(
            reverse("users:user_task_download", args=["journal.ndjsonexporter"])
        )

    assert response.status_code == 200
    assert "X-Accel-Redirect" not in response
    assert _read(response) == archive.read_bytes()
    assert response["Content-Type"] == "application/zip"
    assert f"{task.filename}.zip" in response["Content-Disposition"]
    assert response["Content-Disposition"].startswith("attachment;")


def test_export_download_survives_an_absolute_media_url(tmp_path):
    """What an S3 backend looks like: MEDIA_URL is the bucket, MEDIA_ROOT is not."""
    user, client = _member("s3exporter")
    archive = tmp_path / "export.zip"
    archive.write_bytes(b"payload")
    _completed_export(user, str(archive))

    with override_settings(
        MEDIA_ROOT=str(tmp_path), MEDIA_URL="https://cdn.example.com/"
    ):
        response = client.get(
            reverse("users:user_task_download", args=["journal.ndjsonexporter"])
        )

    assert response.status_code == 200
    assert _read(response) == b"payload"


def test_export_download_redirects_when_the_file_is_gone(tmp_path):
    user, client = _member("goneexporter")
    _completed_export(user, str(tmp_path / "missing.zip"))

    response = client.get(
        reverse("users:user_task_download", args=["journal.ndjsonexporter"])
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("users:data")


def test_marks_export_download_streams_the_workbook(tmp_path):
    user, client = _member("marksexporter")
    workbook = tmp_path / "marks.xlsx"
    workbook.write_bytes(b"PK\x03\x04sheet")
    task = DoufenExporter.create(user=user)
    task.metadata["file"] = str(workbook)
    task.state = Task.States.complete
    task.save()

    response = client.get(reverse("users:export_marks"))

    assert response.status_code == 200
    assert _read(response) == b"PK\x03\x04sheet"
    assert response["Content-Type"] == "application/vnd.ms-excel"
    assert 'filename="marks.xlsx"' in response["Content-Disposition"]


def test_marks_export_download_redirects_without_a_file():
    user, client = _member("marksless")
    task = DoufenExporter.create(user=user)
    task.state = Task.States.complete
    task.save()

    response = client.get(reverse("users:export_marks"))

    assert response.status_code == 302
    assert response["Location"] == reverse("users:data")


@pytest.mark.parametrize(
    "importer,url_name,hint,stem",
    [
        (RymImporter, "users:rym_download", "my_rym.csv", "my_rym"),
        (
            StoryGraphImporter,
            "users:storygraph_download",
            "my_sg.csv",
            "my_sg",
        ),
    ],
)
def test_matched_csv_download_streams_the_file(
    tmp_path, importer, url_name, hint, stem
):
    """The matched CSV sits beside the upload in sync/, which stays local."""
    user, client = _member(f"matched{importer.__name__.lower()}")
    matched = tmp_path / "matched.csv"
    matched.write_text("title,link\nDune,https://example.com/dune\n")
    task = importer.create(user=user)
    task.metadata.update(
        {"phase": "preview", "matched_file": str(matched), "filename_hint": hint}
    )
    task.save()

    response = client.get(reverse(url_name))

    assert response.status_code == 200
    assert "X-Accel-Redirect" not in response
    assert _read(response).decode() == matched.read_text()
    assert response["Content-Type"] == "text/csv"
    assert f'filename="{stem}-matched.csv"' in response["Content-Disposition"]
