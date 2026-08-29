"""Regression tests for neodb-social/neodb#1806.

Wikidata descriptions are short disambiguators, not synopses. They must not
win over a richer source, whichever order the resources were fetched in.
"""

import pytest
from django.utils import translation

from catalog.models import ExternalResource, Game, IdType

WIKIDATA_BLURB = "2009 visual novel game"
IGDB_SYNOPSIS = "Steins;Gate is a Japanese visual novel developed by 5pb."


def _make_game() -> Game:
    return Game.objects.create(
        title="Steins;Gate",
        localized_title=[{"lang": "en", "text": "Steins;Gate"}],
    )


def _add_resource(
    item: Game, id_type: str, id_value: str, description: str
) -> ExternalResource:
    res = ExternalResource.objects.create(
        item=item,
        id_type=id_type,
        id_value=id_value,
        url=f"https://example.org/{id_type}/{id_value}",
    )
    res.metadata = {
        "title": "Steins;Gate",
        "localized_title": [{"lang": "en", "text": "Steins;Gate"}],
        "localized_description": [{"lang": "en", "text": description}],
    }
    res.save()
    return res


def _reload(item: Game) -> Game:
    """display_description is a cached_property; re-read the item so an
    assertion never sees a value cached before the last merge."""
    return Game.objects.get(pk=item.pk)


@pytest.mark.django_db(databases="__all__")
class TestWikidataDescriptionDemotion:
    def test_richer_source_first_then_wikidata(self):
        game = _make_game()
        igdb = _add_resource(game, IdType.IGDB, "steins-gate", IGDB_SYNOPSIS)
        game.merge_data_from_external_resource(igdb)
        wd = _add_resource(game, IdType.WikiData, "Q482494", WIKIDATA_BLURB)
        game.merge_data_from_external_resource(wd)
        with translation.override("en"):
            assert _reload(game).display_description == IGDB_SYNOPSIS

    def test_wikidata_first_then_richer_source(self):
        """The order that actually broke neodb.social: Wikidata merged first,
        so its blurb sat ahead of the synopsis in localized_description."""
        game = _make_game()
        wd = _add_resource(game, IdType.WikiData, "Q482494", WIKIDATA_BLURB)
        game.merge_data_from_external_resource(wd)
        with translation.override("en"):
            assert _reload(game).display_description == WIKIDATA_BLURB
        igdb = _add_resource(game, IdType.IGDB, "steins-gate", IGDB_SYNOPSIS)
        game.merge_data_from_external_resource(igdb)
        with translation.override("en"):
            assert _reload(game).display_description == IGDB_SYNOPSIS

    def test_wikidata_only_item_keeps_blurb(self):
        game = _make_game()
        wd = _add_resource(game, IdType.WikiData, "Q482494", WIKIDATA_BLURB)
        game.merge_data_from_external_resource(wd)
        fresh = _reload(game)
        assert fresh.localized_description == [{"lang": "en", "text": WIKIDATA_BLURB}]
        with translation.override("en"):
            assert fresh.display_description == WIKIDATA_BLURB

    def test_user_authored_description_survives_merge(self):
        """Demotion reorders, it never drops entries not backed by a resource."""
        game = _make_game()
        game.localized_description = [{"lang": "en", "text": "hand written"}]
        game.save()
        wd = _add_resource(game, IdType.WikiData, "Q482494", WIKIDATA_BLURB)
        game.merge_data_from_external_resource(wd)
        fresh = _reload(game)
        texts = [d["text"] for d in fresh.localized_description]
        assert texts == ["hand written", WIKIDATA_BLURB]
        with translation.override("en"):
            assert fresh.display_description == "hand written"


@pytest.mark.django_db(databases="__all__")
class TestLocalizedTextMasking:
    def test_empty_entry_does_not_mask_later_entry(self):
        """catalog/forms.py seeds an empty localized_description entry when an
        item is edited with no description; it must not hide a later one."""
        game = _make_game()
        game.localized_description = [
            {"lang": "en", "text": ""},
            {"lang": "en", "text": IGDB_SYNOPSIS},
        ]
        with translation.override("en"):
            assert game.display_description == IGDB_SYNOPSIS

    def test_empty_title_entry_does_not_mask_later_entry(self):
        game = Game.objects.create(title="Steins;Gate")
        game.localized_title = [
            {"lang": "en", "text": ""},
            {"lang": "en", "text": "Steins;Gate"},
        ]
        with translation.override("en"):
            assert game.get_localized_title() == "Steins;Gate"

    def test_falls_through_to_next_locale_when_all_empty(self):
        game = _make_game()
        game.localized_description = [
            {"lang": "zh-cn", "text": ""},
            {"lang": "en", "text": IGDB_SYNOPSIS},
        ]
        with translation.override("zh-hans"):
            assert game.display_description == IGDB_SYNOPSIS
