"""Custom emoji resolve their image through the takahe media storage.

The emoji file is the one takahe file field neodb used to address by pasting
``TAKAHE_MEDIA_URL`` in front of the name. That is right only while the
setting names the place the files are in, which is not the case for an s3
instance that leaves it at its default.

``FileField`` calls a callable ``storage`` once, when the field is built, so
what the field holds is the instance of that moment. Patching
``storages["takahe"]`` reaches it only until something replaces the registry,
which every ``STORAGES`` override does. These patch the field's own storage.
"""

from unittest import mock

import pytest

from takahe.models import Emoji


@pytest.fixture
def store():
    return Emoji.file.field.storage


@pytest.fixture
def emoji():
    return Emoji(
        shortcode="neodb",
        local=True,
        public=True,
        mimetype="image/png",
        file="emoji/2026/9/20/neodb.png",
    )


def test_url_comes_from_the_takahe_store(emoji, store):
    """A local backend keeps serving emoji from the takahe media mount."""
    with mock.patch.object(store, "base_url", "/media/"):
        url = emoji.full_url()
    assert url.relative == "/media/emoji/2026/9/20/neodb.png"
    assert url.absolute.endswith("/media/emoji/2026/9/20/neodb.png")
    assert "://" in url.absolute


def test_url_follows_the_store_off_the_site(emoji, store):
    """A bucket serves them itself, wherever TAKAHE_MEDIA_URL happens to point."""
    with mock.patch.object(store, "base_url", "https://media.example.org/"):
        url = emoji.full_url()
    assert url.absolute == "https://media.example.org/emoji/2026/9/20/neodb.png"


def test_a_remote_emoji_without_a_file_is_proxied(emoji):
    emoji.file = None
    emoji.remote_url = "https://remote.example/emoji/x.png"
    assert emoji.full_url().relative.startswith("/proxy/emoji/")
