import pytest
from django.test import Client, override_settings
from django.urls import reverse

from journal.exporters import NdjsonExporter
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
