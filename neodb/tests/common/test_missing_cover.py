import pytest

from common.models.misc import MISSING_COVER, is_missing_cover


@pytest.mark.parametrize(
    "name",
    [
        "",
        None,
        MISSING_COVER,
        # markers left by older releases, never stored as files
        "collection/default.svg",
        "default.svg",
        "item/book/default.svg",
    ],
)
def test_missing_cover_markers(name):
    assert is_missing_cover(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "user/1/2026/01/01/0f2a1f7e-6c2a-4b8a-9d1f-2b3c4d5e6f70.jpg",
        "item/book/2024/09/06/c798ab0e-ff7f-45e7-99ce-df0ef2aec090.png",
        "user/1/2026/01/01/default.png",
    ],
)
def test_real_covers_are_not_missing(name):
    assert is_missing_cover(name) is False
