import pytest

from catalog.common.sites import SiteManager
from catalog.management.commands.seed_catalog import SEED_ITEMS
from catalog.models import IdType

EXPECTED_COUNTS = {
    "book": 20,
    "album": 20,
    "game": 20,
    "movie": 20,
    "tv": 10,
}

EXPECTED_ID_TYPES = {
    "book": IdType.GoogleBooks,
    "album": IdType.MusicBrainz_ReleaseGroup,
    "game": IdType.IGDB,
    "movie": IdType.TMDB_Movie,
    "tv": IdType.TMDB_TV,
}


class TestSeedCatalogLinks:
    """The seed links are hand-written, so guard them without any network."""

    def test_counts(self):
        assert set(SEED_ITEMS) == set(EXPECTED_COUNTS)
        for category, expected in EXPECTED_COUNTS.items():
            assert len(SEED_ITEMS[category]) == expected, category

    @pytest.mark.parametrize("category", sorted(EXPECTED_COUNTS))
    def test_urls_resolve_to_expected_site(self, category):
        for name, url in SEED_ITEMS[category]:
            cls = SiteManager.get_class_by_url(url)
            assert cls is not None, f"{category}: {name} ({url})"
            assert cls.ID_TYPE == EXPECTED_ID_TYPES[category], (
                f"{category}: {name} resolved to {cls.ID_TYPE}"
            )

    def test_urls_are_unique(self):
        urls = [url for entries in SEED_ITEMS.values() for _, url in entries]
        assert len(urls) == len(set(urls))

    def test_ids_parse(self):
        """A typo'd id can still match the URL pattern, so check each site
        object actually extracts a non-empty id."""
        for category, entries in SEED_ITEMS.items():
            for name, url in entries:
                site = SiteManager.get_site_by_url(url, detect_redirection=False)
                assert site is not None, f"{category}: {name}"
                assert site.id_value, f"{category}: {name} ({url})"
