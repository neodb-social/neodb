"""
Tests for the WikiData site implementation
"""

from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from catalog.common import ParseError
from catalog.common.downloaders import (
    BasicDownloader,
    DownloadError,
    use_local_response,
)
from catalog.models import (
    Game,
    IdType,
    Movie,
    People,
    Performance,
    Podcast,
    PodcastEpisode,
    TVEpisode,
    TVSeason,
    TVShow,
    Work,
)
from catalog.sites.wikidata import (
    _PARENT_TYPE_CACHE,
    WikiData,
    WikidataProperties,
    WikidataTypes,
)


@pytest.fixture(autouse=True)
def clear_parent_type_cache():
    """Subclass graphs mocked by one test must not leak into the next."""
    _PARENT_TYPE_CACHE.clear()
    yield
    _PARENT_TYPE_CACHE.clear()


def v1_statement(content, rank: str = "normal") -> dict:
    """One statement in the shape the Wikibase REST v1 API returns."""
    return {"rank": rank, "value": {"type": "value", "content": content}}


def v1_payload(
    entity_id: str,
    instance_of: list[str] | None = None,
    subclass_of: list[str] | None = None,
    statements: dict | None = None,
    labels: dict | None = None,
    descriptions: dict | None = None,
) -> dict:
    """A raw v1 payload, as _fetch_entity_by_id returns it."""
    stmts = {}
    if instance_of:
        stmts[WikidataProperties.P31] = [v1_statement(q) for q in instance_of]
    if subclass_of:
        stmts[WikidataProperties.P279] = [v1_statement(q) for q in subclass_of]
    stmts.update(statements or {})
    return {
        "id": entity_id,
        "type": "item",
        "labels": labels or {},
        "descriptions": descriptions or {},
        "statements": stmts,
    }


def entity(entity_id: str, **kwargs) -> dict:
    """A normalized entity, the shape every extractor consumes."""
    return WikiData._normalize_entity(v1_payload(entity_id, **kwargs))


def site_for(entity_id: str) -> WikiData:
    return WikiData(url=f"https://www.wikidata.org/wiki/{entity_id}")


def assert_entity_type_mapping(entity_id, entity_types, expected_model):
    """Assert the model selected for an entity's 'instance of' values."""
    if isinstance(entity_types, str):
        entity_types = [entity_types]
    site = site_for(entity_id)
    entity_data = entity(entity_id, instance_of=entity_types)
    assert site._determine_entity_type(entity_data) == expected_model


# Group 1: Basic entity type detection tests
def test_basic_entity_type_detection():
    """Test model detection for common entity types using the helper function"""
    # Movie tests
    assert_entity_type_mapping("Q184843", WikidataTypes.FILM, Movie)
    assert_entity_type_mapping("Q226730", WikidataTypes.SILENT_FILM, Movie)
    assert_entity_type_mapping("Q506240", WikidataTypes.TV_FILM, Movie)
    assert_entity_type_mapping("Q220898", WikidataTypes.OVA, Movie)
    assert_entity_type_mapping("Q24862", WikidataTypes.SHORT_FILM, Movie)
    assert_entity_type_mapping("Q18011172", WikidataTypes.FILM_PROJECT, Movie)

    # Book/Work tests
    assert_entity_type_mapping("Q721", WikidataTypes.LITERARY_WORK, Work)
    assert_entity_type_mapping("Q722", WikidataTypes.NOVEL, Work)
    assert_entity_type_mapping("Q45340", WikidataTypes.MEDIA_FRANCHISE, Work)

    # TV show tests
    assert_entity_type_mapping("Q1079", WikidataTypes.TV_SERIES, TVShow)
    assert_entity_type_mapping("Q15416", WikidataTypes.TV_PROGRAM, TVShow)
    assert_entity_type_mapping("Q117467246", WikidataTypes.ANIMATED_TV_SERIES, TVShow)
    assert_entity_type_mapping("Q581714", WikidataTypes.ANIMATED_SERIES, TVShow)
    assert_entity_type_mapping("Q1259759", WikidataTypes.TV_MINISERIES, TVShow)
    assert_entity_type_mapping("Q113687694", WikidataTypes.OVA_SERIES, TVShow)
    assert_entity_type_mapping("Q113671041", WikidataTypes.ONA_SERIES, TVShow)

    # TV seasons and episodes
    assert_entity_type_mapping("Q25361", WikidataTypes.TV_SEASON, TVSeason)
    assert_entity_type_mapping("Q53234", WikidataTypes.TV_EPISODE, TVEpisode)

    # Game test
    assert_entity_type_mapping("Q7889", WikidataTypes.VIDEO_GAME, Game)

    # Podcast tests
    assert_entity_type_mapping("Q24634210", WikidataTypes.PODCAST_SHOW, Podcast)
    assert_entity_type_mapping(
        "Q61855877", WikidataTypes.PODCAST_EPISODE, PodcastEpisode
    )

    # Performance tests
    assert_entity_type_mapping("Q25379", WikidataTypes.PLAY, Performance)
    assert_entity_type_mapping("Q2743", WikidataTypes.MUSICAL, Performance)
    assert_entity_type_mapping("Q1344", WikidataTypes.OPERA, Performance)

    # People test
    assert_entity_type_mapping("Q42", WikidataTypes.HUMAN, People)


# Group 2: Multiple entity type tests
def test_multiple_entity_types():
    """Test entities with multiple types and priority rules"""
    # TV_SPECIAL has priority over TV_EPISODE
    assert_entity_type_mapping(
        "Q53235", [WikidataTypes.TV_EPISODE, WikidataTypes.TV_SPECIAL], Movie
    )

    # TV_SERIES should have priority over TV_PROGRAM by first match
    assert_entity_type_mapping(
        "Q53236", [WikidataTypes.TV_PROGRAM, WikidataTypes.TV_SERIES], TVShow
    )


# Group 3: Parent type lookup tests
def test_parent_type_lookup():
    """Test model detection using parent type lookup"""
    # 'subclass of' carried by the entity itself
    entity_data = entity(
        "Q999999",
        instance_of=["Q12345"],  # Unknown instance type
        subclass_of=[WikidataTypes.FILM],  # Known parent type (Film)
    )
    site = site_for("Q999999")
    with patch.object(site, "_fetch_entity_by_id", side_effect={}.get):
        assert site._determine_entity_type(entity_data) == Movie

    # 'subclass of' of the instance class, which needs an API lookup
    entity_data = entity("Q999998", instance_of=["Q12346"])
    graph = {"Q12346": v1_payload("Q12346", subclass_of=[WikidataTypes.TV_SERIES])}
    site = site_for("Q999998")
    with patch.object(site, "_fetch_entity_by_id", side_effect=graph.get):
        assert site._determine_entity_type(entity_data) == TVShow


def test_recursive_parent_type_lookup():
    """Test model detection using recursive parent type lookup"""
    entity_data = entity("Q999997", instance_of=["Q12347"], subclass_of=["Q54321"])
    # A deeper hierarchy: Q12347 -> Q98765 -> Q54321 -> PODCAST_SHOW
    graph = {
        "Q12347": v1_payload("Q12347", subclass_of=["Q98765"]),
        "Q98765": v1_payload("Q98765", subclass_of=["Q54321"]),
        "Q54321": v1_payload("Q54321", subclass_of=[WikidataTypes.PODCAST_SHOW]),
    }

    site = site_for("Q999997")
    with patch.object(site, "_fetch_entity_by_id", side_effect=graph.get):
        assert site._determine_entity_type(entity_data) == Podcast


def test_ambiguous_ancestor_does_not_map_to_book():
    """A type whose only mapped ancestor is 'creative work' stays unsupported.

    Regression for radio shows: 'radio series' (Q14623351) is not mapped, and
    walking up its subclass graph reaches 'creative work', which used to turn
    every such entity into a book Work.
    """
    radio_series = "Q14623351"
    radio_program = "Q1555508"
    series_of_creative_works = "Q7725310"

    entity_data = entity("Q2388264", instance_of=[radio_series])
    graph = {
        radio_series: v1_payload(
            radio_series, subclass_of=[radio_program, series_of_creative_works]
        ),
        radio_program: v1_payload(
            radio_program,
            subclass_of=["Q11578774", "Q11033", "Q110879422", "Q119649004"],
        ),
        series_of_creative_works: v1_payload(
            series_of_creative_works,
            subclass_of=["Q17489659", WikidataTypes.CREATIVE_WORK],
        ),
    }

    site = site_for("Q2388264")
    with patch.object(site, "_fetch_entity_by_id", side_effect=graph.get):
        with pytest.raises(ParseError):
            site._determine_entity_type(entity_data)

    # 'creative work' is still honoured as a direct 'instance of' value
    assert_entity_type_mapping("Q999996", WikidataTypes.CREATIVE_WORK, Work)


def test_nearest_ancestor_wins():
    """The closest mapped ancestor decides the model, and does so every run."""
    entity_data = entity("Q999995", instance_of=["Q12348"])
    graph = {
        # one hop up: TV series; two hops up: novel
        "Q12348": v1_payload("Q12348", subclass_of=[WikidataTypes.TV_SERIES, "Q12349"]),
        "Q12349": v1_payload("Q12349", subclass_of=[WikidataTypes.NOVEL]),
    }

    site = site_for("Q999995")
    with patch.object(site, "_fetch_entity_by_id", side_effect=graph.get):
        assert site._determine_entity_type(entity_data) == TVShow


def test_nearest_ancestor_across_branches_wins():
    """An ancestor one hop above an instance class beats one two hops up."""
    entity_data = entity("Q999990", instance_of=["Q12352"], subclass_of=["Q12353"])
    graph = {
        "Q12352": v1_payload("Q12352", subclass_of=[WikidataTypes.FILM]),
        "Q12353": v1_payload("Q12353", subclass_of=["Q12354"]),
        "Q12354": v1_payload("Q12354", subclass_of=[WikidataTypes.NOVEL]),
    }

    site = site_for("Q999990")
    with patch.object(site, "_fetch_entity_by_id", side_effect=graph.get):
        assert site._determine_entity_type(entity_data) == Movie


def test_classification_survives_fetch_failure():
    """An unreachable class is skipped, not fatal, so other branches still match."""
    entity_data = entity("Q999991", instance_of=["Q12350", "Q12351"])
    graph = {"Q12351": v1_payload("Q12351", subclass_of=[WikidataTypes.FILM])}

    def fetch(entity_id):
        if entity_id == "Q12350":
            raise DownloadError(BasicDownloader(WikiData.id_to_url(entity_id)))
        return graph.get(entity_id)

    site = site_for("Q999991")
    with patch.object(site, "_fetch_entity_by_id", side_effect=fetch):
        assert site._determine_entity_type(entity_data) == Movie


# Group 4: Statement normalization tests
def test_entity_types_and_parent_types():
    """Test extraction of 'instance of' and 'subclass of' values"""
    entity_data = entity(
        "Q999999",
        instance_of=["Q12345", "Q67890"],
        subclass_of=[WikidataTypes.TV_SERIES],
    )

    site = site_for("Q999999")
    instance_types = site._extract_entity_types(entity_data, WikidataProperties.P31)
    parent_types = site._extract_entity_types(entity_data, WikidataProperties.P279)
    with patch.object(site, "_fetch_entity_by_id", side_effect={}.get):
        model = site._determine_entity_type(entity_data)

    assert instance_types == ["Q12345", "Q67890"]
    assert parent_types == [WikidataTypes.TV_SERIES]
    assert model == TVShow  # Should identify as TVShow from parent type


def test_deprecated_rank_is_ignored():
    """A deprecated 'instance of' statement does not classify the entity."""
    entity_data = WikiData._normalize_entity(
        v1_payload(
            "Q999994",
            statements={
                WikidataProperties.P31: [
                    v1_statement(WikidataTypes.FILM, rank="deprecated"),
                    v1_statement(WikidataTypes.TV_SERIES),
                ]
            },
        )
    )

    assert site_for("Q999994")._determine_entity_type(entity_data) == TVShow


def test_preferred_rank_wins():
    """A preferred-rank value hides the normal-rank ones on the same property."""
    entity_data = WikiData._normalize_entity(
        v1_payload(
            "Q999993",
            statements={
                WikidataProperties.P577: [
                    v1_statement({"time": "+1999-03-31T00:00:00Z"}),
                    v1_statement({"time": "+2003-05-15T00:00:00Z"}, rank="preferred"),
                ]
            },
        )
    )

    site = site_for("Q999993")
    assert site._extract_date(entity_data, WikidataProperties.P577) == "2003-05-15"
    assert len(site._extract_property_values(entity_data, WikidataProperties.P577)) == 1


def test_valueless_statements_are_dropped():
    """novalue and somevalue statements carry no content and are skipped."""
    entity_data = WikiData._normalize_entity(
        v1_payload(
            "Q999992",
            statements={
                WikidataProperties.P856: [
                    {"rank": "normal", "value": {"type": "somevalue"}},
                    v1_statement("https://example.org"),
                ]
            },
        )
    )

    url = site_for("Q999992")._extract_url(entity_data, WikidataProperties.P856)
    assert url == "https://example.org"


# Group 5: Edge case and error tests
def test_edge_cases_and_errors():
    """Test edge cases and error handling"""
    # Test person entity - should map to People
    assert_entity_type_mapping("Q42", WikidataTypes.HUMAN, People)

    site = site_for("Q42")

    # Test entity with no instance of properties
    with pytest.raises(ParseError):
        site._determine_entity_type(entity("Q12345"))

    # Test unsupported entity type with no known ancestor
    with patch.object(site, "_fetch_entity_by_id", side_effect={}.get):
        with pytest.raises(ParseError):
            site._determine_entity_type(entity("Q123456", instance_of=["Q123"]))


# Group 6: Scrape behaviour tests
def test_scrape_accepts_redirected_payload():
    """A merged QID redirects; the target's content is kept under the old QID."""
    site = site_for("Q999989")
    payload = v1_payload(
        "Q83495", instance_of=[WikidataTypes.FILM], labels={"en": "The Matrix"}
    )

    with patch("catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS", ["en"]):
        with patch.object(site, "_fetch_entity_by_id", return_value=payload):
            content = site.scrape()

    assert content.metadata["title"] == "The Matrix"
    assert content.metadata["preferred_model"] == "Movie"


def test_scrape_rejects_payload_without_id():
    """A payload that is not an entity is still a parse error."""
    site = site_for("Q999989")
    with patch.object(site, "_fetch_entity_by_id", return_value={"error": "not-found"}):
        with pytest.raises(ParseError):
            site.scrape()


def test_title_falls_back_outside_preferred_languages():
    """Without a preferred-language label, English wins, then any other label."""
    site = site_for("Q999988")

    def scrape_with_labels(labels):
        payload = v1_payload("Q999988", instance_of=[WikidataTypes.FILM], labels=labels)
        with patch("catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS", ["zh"]):
            with patch.object(site, "_fetch_entity_by_id", return_value=payload):
                return site.scrape()

    content = scrape_with_labels({"fr": "Matrice", "en": "The Matrix"})
    assert content.metadata["title"] == "The Matrix"
    assert content.metadata["localized_title"] == [{"lang": "en", "text": "The Matrix"}]

    content = scrape_with_labels({"fr": "Matrice"})
    assert content.metadata["title"] == "Matrice"
    assert content.metadata["localized_title"] == [{"lang": "fr", "text": "Matrice"}]

    content = scrape_with_labels({})
    assert content.metadata["title"] == "Q999988"
    assert content.metadata["localized_title"] == []


def test_sparql_string_escaping():
    """An external ID cannot break out of the SPARQL literal it is placed in."""
    assert WikiData._escape_sparql_string("tt0133093") == "tt0133093"
    assert WikiData._escape_sparql_string('a"b') == 'a\\"b'
    assert WikiData._escape_sparql_string("a\\b") == "a\\\\b"
    # backslashes are escaped before quotes, so an escaped quote stays escaped
    assert WikiData._escape_sparql_string('\\"') == '\\\\\\"'


def test_preferred_model_in_metadata():
    """Test preferred model is included in metadata"""
    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q184843")
    content = wiki_site.scrape()
    assert "preferred_model" in content.metadata
    assert content.metadata["preferred_model"] == "Movie"


# Group 7: Language handling tests
def test_language_handling():
    """Test language handling in labels and descriptions extraction"""
    # Test preferred labels extraction
    with patch(
        "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
        ["en", "zh", "zh-cn", "zh-tw"],
    ):
        # Mock entity data with labels in multiple languages
        entity_data = entity(
            "Q42",
            labels={
                "en": "Douglas Adams",
                "zh": "道格拉斯·亚当斯",
                "zh-cn": "道格拉斯·亚当斯",
                "zh-tw": "道格拉斯·亞當斯",
                "de": "Douglas Adams",
                "fr": "Douglas Adams",
                "es": "Douglas Adams",
                "ja": "ダグラス・アダムズ",
            },
        )

        labels = site_for("Q42")._extract_labels(entity_data)

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

    # Test preferred descriptions extraction
    with patch(
        "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
        ["en", "zh", "zh-cn", "zh-tw"],
    ):
        # Mock entity data with descriptions in multiple languages
        entity_data = entity(
            "Q42",
            descriptions={
                "en": "English writer and humorist",
                "zh": "英国作家",
                "zh-cn": "英国作家",
                "zh-tw": "英國作家",
                "de": "britischer Science-Fiction-Autor",
                "fr": "écrivain de science-fiction",
            },
        )

        descriptions = site_for("Q42")._extract_descriptions(entity_data)

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
    """Test language expansion for preferred languages"""
    with patch("catalog.sites.wikidata.SITE_PREFERRED_LANGUAGES", ["en", "zh"]):
        from catalog.sites.wikidata import _get_preferred_languages

        preferred_langs = _get_preferred_languages()
        assert "en" in preferred_langs
        assert "zh" in preferred_langs
        assert "zh-hans" in preferred_langs
        assert "zh-hant" in preferred_langs


class TestWikiData:
    @use_local_response
    def test_url_parsing(self):
        movie_url = "https://www.wikidata.org/wiki/Q83495"  # The Matrix
        site = WikiData(url=movie_url)
        assert site.url == movie_url
        assert site.ID_TYPE == IdType.WikiData
        assert site.id_value == "Q83495"
        alt_url = "https://www.wikidata.org/entity/Q83495"
        site2 = WikiData(url=alt_url)
        assert site2.id_value == "Q83495"

    @patch(
        "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
        ["en", "zh", "zh-hans", "zh-hant"],
    )
    @use_local_response
    def test_scrape_movie(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q83495")
        content = site.scrape()
        assert content.metadata["title"] == "The Matrix"
        localized_titles = content.metadata["localized_title"]
        assert any(
            t["lang"] == "en" and t["text"] == "The Matrix" for t in localized_titles
        )
        assert any(
            t["lang"] == "zh" and t["text"] == "黑客帝国" for t in localized_titles
        )
        assert any(
            t["lang"] == "zh-hans" and t["text"] == "黑客帝国" for t in localized_titles
        )
        descriptions = content.metadata["localized_description"]
        assert any(
            d["lang"] == "en" and "1999" in d["text"] and "film" in d["text"]
            for d in descriptions
        )
        assert content.metadata["preferred_model"] == "Movie"
        assert "cover_image_url" in content.metadata
        assert (
            content.metadata["cover_image_url"]
            == "https://commons.wikimedia.org/wiki/Special:FilePath/The.Matrix.glmatrix.1.png?width=1000"
        )
        assert content.metadata["release_date"] == "1999-03-31"
        assert content.lookup_ids.get(IdType.IMDB) == "tt0133093"
        assert content.lookup_ids.get(IdType.TMDB_Movie) == "603"
        assert content.lookup_ids.get(IdType.DoubanMovie) == "1291843"

    @use_local_response
    def test_v1_api_format(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q83495")
        content = site.scrape()
        assert content.metadata["preferred_model"] == "Movie"
        assert (
            content.metadata["cover_image_url"]
            == "https://commons.wikimedia.org/wiki/Special:FilePath/The.Matrix.glmatrix.1.png?width=1000"
        )

    @use_local_response
    def test_scrape_game(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q3182559")
        content = site.scrape()
        assert content.metadata["title"] == "Cyberpunk 2077"
        assert content.metadata["preferred_model"] == "Game"
        assert content.metadata["release_date"] == "2020-12-10"
        assert content.metadata["official_site"] == "https://www.cyberpunk.net"
        assert content.lookup_ids.get(IdType.Steam) == "1091500"
        assert content.lookup_ids.get(IdType.DoubanGame) == "25931998"

    @use_local_response
    def test_scrape_performance(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q41567")
        content = site.scrape()
        assert content.metadata["title"] == "Hamlet"
        assert content.metadata["preferred_model"] == "Performance"
        # assert "Q692" in content.metadata["playwright"]  # Shakespeare
        # assert "Q1860" in content.metadata["language"]  # English
        # assert "Q80930" in content.metadata["genre"]  # Tragedy
        assert (
            content.metadata["opening_date"] == "1602"
        )  # 1602-00-00 keeps year precision


def test_extract_openlibrary_ids():
    """Test extraction of OpenLibrary work IDs from WikiData"""
    # Create a WikiData site instance
    site = WikiData(id_value="Q12345")

    # Mock entity data with OpenLibrary work ID (P648)
    entity_data = entity("Q12345", statements={"P648": [v1_statement("OL8694710W")]})

    # Extract external IDs
    resources = site._extract_external_ids(entity_data)

    # Find OpenLibrary_Work in the extracted resources
    openlibrary_work = None
    for resource in resources:
        if resource["id_type"] == IdType.OpenLibrary_Work:
            openlibrary_work = resource
            break

    # Verify OpenLibrary work ID was extracted correctly
    assert openlibrary_work is not None
    assert openlibrary_work["id_value"] == "OL8694710W"
    assert openlibrary_work["id_type"] == IdType.OpenLibrary_Work


def test_openlibrary_author_id_detection():
    """Test that OL...A author IDs are correctly detected"""
    from catalog.sites.openlibrary import OpenLibrary

    assert OpenLibrary.guess_id_type("OL34184A") == IdType.OpenLibrary_Author
    assert OpenLibrary.guess_id_type("OL8694710W") == IdType.OpenLibrary_Work
    assert OpenLibrary.guess_id_type("OL7353617M") == IdType.OpenLibrary


class TestWikiDataPerson:
    @use_local_response
    def test_scrape_person(self):
        """Test scraping Wikidata Q42 (Douglas Adams) as a People entity"""
        site = WikiData(url="https://www.wikidata.org/wiki/Q42")
        content = site.scrape()

        # Should detect as People model
        assert content.metadata["preferred_model"] == "People"

        # localized_name (remapped from localized_title)
        assert "localized_name" in content.metadata
        assert "localized_title" not in content.metadata
        names = content.metadata["localized_name"]
        assert any(n["lang"] == "en" and n["text"] == "Douglas Adams" for n in names)

        # localized_bio (remapped from localized_description)
        assert "localized_bio" in content.metadata
        assert "localized_description" not in content.metadata

        # Birth and death dates
        assert content.metadata["birth_date"] == "1952-03-11"
        assert content.metadata["death_date"] == "2001-05-11"

        # Official site
        assert content.metadata["official_site"] == "https://douglasadams.com"

        # Cover image
        assert content.metadata.get("cover_image_url") is not None

        # External ID lookup_ids
        assert content.lookup_ids.get(IdType.IMDB) == "nm0010930"
        assert content.lookup_ids.get(IdType.TMDB_Person) == "52843"


class TestTMDBPerson:
    def test_url_parsing(self):
        from catalog.sites.tmdb import TMDB_Person

        assert TMDB_Person.ID_TYPE == IdType.TMDB_Person
        assert TMDB_Person.DEFAULT_MODEL == People
        assert (
            TMDB_Person.id_to_url("17419") == "https://www.themoviedb.org/person/17419"
        )
        site = TMDB_Person(url="https://www.themoviedb.org/person/17419")
        assert site.id_value == "17419"

    @use_local_response
    def test_scrape_person(self):
        """Test scraping TMDB person 17419 (Bryan Cranston) with real cached data"""
        from catalog.sites.tmdb import TMDB_Person

        site = TMDB_Person(url="https://www.themoviedb.org/person/17419")
        content = site.scrape()

        assert content.metadata["title"] == "Bryan Cranston"
        assert content.metadata["birth_date"] == "1956-03-07"
        assert content.metadata["death_date"] is None

        # Cover image (profile photo)
        assert content.metadata["cover_image_url"] is not None
        assert (
            urlparse(content.metadata["cover_image_url"]).hostname == "image.tmdb.org"
        )

        # Verify localized_name (not localized_title)
        assert any(
            n["text"] == "Bryan Cranston" for n in content.metadata["localized_name"]
        )
        # Verify localized_bio (not localized_description)
        assert any("Bryan" in b["text"] for b in content.metadata["localized_bio"])

        # External IDs
        assert content.lookup_ids.get(IdType.IMDB) == "nm0186505"
        assert content.lookup_ids.get(IdType.WikiData) == "Q23547"


class TestGoodreadsAuthor:
    def test_url_parsing(self):
        from catalog.sites.goodreads import Goodreads_Author

        assert Goodreads_Author.ID_TYPE == IdType.Goodreads_Author
        assert Goodreads_Author.DEFAULT_MODEL == People
        assert (
            Goodreads_Author.id_to_url("874602")
            == "https://www.goodreads.com/author/show/874602"
        )
        site = Goodreads_Author(
            url="https://www.goodreads.com/author/show/874602.Ursula_K_Le_Guin"
        )
        assert site.id_value == "874602"

    @use_local_response
    def test_scrape_author(self):
        """Test scraping Goodreads author 874602 (Ursula K. Le Guin) with real cached data"""
        from catalog.sites.goodreads import Goodreads_Author

        site = Goodreads_Author(
            url="https://www.goodreads.com/author/show/874602.Ursula_K_Le_Guin"
        )
        content = site.scrape()

        assert content.metadata["title"] == "Ursula K. Le Guin"

        # Localized name
        names = content.metadata["localized_name"]
        assert any(n["text"] == "Ursula K. Le Guin" for n in names)

        # Bio
        assert len(content.metadata["localized_bio"]) > 0
        assert "novel" in content.metadata["localized_bio"][0]["text"].lower()

        # Birth and death dates
        assert content.metadata["birth_date"] == "1929-10-21"
        assert content.metadata["death_date"] == "2018-01-22"

        # Website
        assert content.metadata["official_site"] == "http://www.ursulakleguin.com/"

        # Cover image
        assert content.metadata["cover_image_url"] is not None
        cover_host = urlparse(content.metadata["cover_image_url"]).hostname or ""
        assert cover_host == "gr-assets.com" or cover_host.endswith(".gr-assets.com")

    def test_parse_date(self):
        from catalog.sites.goodreads import Goodreads_Author

        assert Goodreads_Author._parse_date("October 21, 1929") == "1929-10-21"
        assert Goodreads_Author._parse_date("January 22, 2018") == "2018-01-22"
        assert Goodreads_Author._parse_date("March 1964") == "1964-03"
        assert Goodreads_Author._parse_date("") is None
        assert not Goodreads_Author._parse_date("  ")
