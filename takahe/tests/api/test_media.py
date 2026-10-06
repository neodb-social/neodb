import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from activities.models import PostAttachment


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = str(tmp_path)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,content_type",
    [
        ("p.html", "text/html"),
        ("p.svg", "application/octet-stream"),
        ("p.html", "application/octet-stream"),
        ("p.svg", "image/svg+xml"),
        ("p.html", "video/x-not-a-real-type"),
    ],
)
def test_upload_rejects_unsafe_types(api_client, name: str, content_type: str):
    upload = SimpleUploadedFile(name, b"<script>alert(1)</script>", content_type)
    response = api_client.post("/api/v1/media", {"file": upload})
    assert response.status_code == 400
    assert not PostAttachment.objects.exists()


@pytest.mark.django_db
def test_upload_media_ignores_client_extension(api_client):
    upload = SimpleUploadedFile("p.html", b"\x00\x00\x00\x18ftypmp42", "video/mp4")
    response = api_client.post("/api/v1/media", {"file": upload})
    assert response.status_code == 200
    attachment = PostAttachment.objects.get(pk=response.json()["id"])
    assert attachment.mimetype == "video/mp4"
    assert attachment.file.name.endswith(".mp4")


@pytest.mark.django_db
def test_upload_image(api_client):
    upload = SimpleUploadedFile("p.html", _png(), "image/png")
    response = api_client.post("/api/v1/media", {"file": upload})
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "image"
    attachment = PostAttachment.objects.get(pk=data["id"])
    assert attachment.mimetype == "image/webp"
    assert attachment.file.name.endswith(".webp")
