from unittest.mock import patch

from catalog.sites.wikidata import WikiData


def test_extract_labels_preferred_only():
    """Test that _extract_labels only includes labels in preferred languages"""
    # Mock entity data with labels in multiple languages
    entity_data = {
        "labels": {
            "en": {"value": "Douglas Adams", "language": "en"},
            "zh": {"value": "道格拉斯·亚当斯", "language": "zh"},
            "zh-cn": {"value": "道格拉斯·亚当斯", "language": "zh-cn"},
            "zh-tw": {"value": "道格拉斯·亞當斯", "language": "zh-tw"},
            "de": {"value": "Douglas Adams", "language": "de"},
            "fr": {"value": "Douglas Adams", "language": "fr"},
            "es": {"value": "Douglas Adams", "language": "es"},
            "ja": {"value": "ダグラス・アダムズ", "language": "ja"},
        }
    }

    # Mock WIKIDATA_PREFERRED_LANGS to test only specific languages
    with patch(
        "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
        ["en", "zh", "zh-cn", "zh-tw"],
    ):
        wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
        labels = wiki_site._extract_labels(entity_data)

        # Verify that only preferred labels are included
        assert "en" in labels
        assert "zh" in labels
        assert "zh-cn" in labels
        assert "zh-tw" in labels
        assert "de" not in labels
        assert "fr" not in labels
        assert labels["en"] == "Douglas Adams"
        assert labels["zh"] == "道格拉斯·亚当斯"
        assert labels["zh-cn"] == "道格拉斯·亚当斯"
        assert labels["zh-tw"] == "道格拉斯·亞當斯"


def test_extract_descriptions_preferred_only():
    """Test that _extract_descriptions only includes descriptions in preferred languages"""
    # Mock entity data with descriptions in multiple languages
    entity_data = {
        "descriptions": {
            "en": {"value": "English writer and humorist", "language": "en"},
            "zh": {"value": "英国作家", "language": "zh"},
            "zh-cn": {"value": "英国作家", "language": "zh-cn"},
            "zh-tw": {"value": "英國作家", "language": "zh-tw"},
            "de": {"value": "britischer Science-Fiction-Autor", "language": "de"},
            "fr": {"value": "écrivain de science-fiction", "language": "fr"},
        }
    }

    # Mock WIKIDATA_PREFERRED_LANGS for testing
    with patch(
        "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
        ["en", "zh", "zh-cn", "zh-tw"],
    ):
        wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
        descriptions = wiki_site._extract_descriptions(entity_data)

        # Verify that only preferred language descriptions are included
        assert len(descriptions) == 4
        assert any(
            d["lang"] == "en" and d["text"] == "English writer and humorist"
            for d in descriptions
        )
        assert any(d["lang"] == "zh" and d["text"] == "英国作家" for d in descriptions)
        assert any(
            d["lang"] == "zh-cn" and d["text"] == "英国作家" for d in descriptions
        )
        assert any(
            d["lang"] == "zh-tw" and d["text"] == "英國作家" for d in descriptions
        )
        assert not any(d["lang"] == "de" for d in descriptions)
        assert not any(d["lang"] == "fr" for d in descriptions)


def test_preferred_languages_expansion():
    """Test that _get_preferred_languages correctly handles Chinese variants"""
    # Mock SITE_PREFERRED_LANGUAGES to have controlled test values
    with patch("catalog.sites.wikidata.SITE_PREFERRED_LANGUAGES", ["en", "zh"]):
        # Import the function directly and call it with the patched value
        from catalog.sites.wikidata import _get_preferred_languages

        preferred_langs = _get_preferred_languages()

        # Assert English is included as-is
        assert "en" in preferred_langs

        # Assert Chinese is expanded to all variants
        assert "zh" in preferred_langs
        assert "zh-cn" in preferred_langs
        assert "zh-tw" in preferred_langs
        assert "zh-hk" in preferred_langs
        assert "zh-hans" in preferred_langs
        assert "zh-hant" in preferred_langs
        assert "zh-sg" in preferred_langs
        assert "zh-mo" in preferred_langs

        # Assert we have exactly the expected number of languages
        # 1 for English + 8 for Chinese variants
        assert len(preferred_langs) == 9
