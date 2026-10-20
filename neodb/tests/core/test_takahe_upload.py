import io
from unittest.mock import patch

import pytest
from PIL import ExifTags, Image

from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.fixture
def identity_pk() -> int:
    return User.register(username="uploader").identity.pk


def _thumbnail_size(attachment) -> tuple[int, int]:
    with attachment.thumbnail.open("rb") as f:
        return Image.open(io.BytesIO(f.read())).size


def test_multi_picture_jpeg(identity_pk):
    """iPhone photos are MPO: a JPEG with extra embedded frames."""
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1200), "red").save(
        buf,
        format="MPO",
        save_all=True,
        append_images=[Image.new("RGB", (800, 600), "blue")],
    )
    a = Takahe.upload_image(identity_pk, "photo.jpeg", buf.getvalue(), "image/jpeg")
    assert (a.width, a.height) == (1600, 1200)
    assert _thumbnail_size(a) == (300, 225)


def _rotated_jpeg() -> bytes:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1200), "red").save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_exif_orientation(identity_pk):
    """Orientation 6 shows the sensor's 1600x1200 as a 1200x1600 portrait."""
    a = Takahe.upload_image(identity_pk, "photo.jpeg", _rotated_jpeg(), "image/jpeg")
    assert (a.width, a.height) == (1200, 1600)
    assert _thumbnail_size(a) == (169, 225)


def test_exif_rewrite_failure_keeps_sizes_consistent(identity_pk):
    """exif_transpose rotates first, then rewrites EXIF, which can raise."""
    content = _rotated_jpeg()
    with patch.object(Image.Exif, "tobytes", side_effect=ValueError("bad exif")):
        a = Takahe.upload_image(identity_pk, "photo.jpeg", content, "image/jpeg")
    assert (a.width, a.height) == (1600, 1200)
    assert _thumbnail_size(a) == (300, 225)


def test_animated_gif_thumbnail_is_resized(identity_pk):
    frames = [Image.new("RGB", (800, 600), c) for c in ("red", "green", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    a = Takahe.upload_image(identity_pk, "anim.gif", buf.getvalue(), "image/gif")
    assert (a.width, a.height) == (800, 600)
    assert _thumbnail_size(a) == (300, 225)
