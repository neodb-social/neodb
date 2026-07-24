import datetime

from dateutil.tz import tzutc

from core.ld import (
    canonicalise,
    get_first_concrete_type,
    get_language,
    parse_ld_date,
)


def test_parse_ld_date():
    """
    Tests that the various kinds of LD dates that we see will work
    """
    difference = parse_ld_date("2022-11-16T15:57:58Z") - datetime.datetime(
        2022,
        11,
        16,
        15,
        57,
        58,
        tzinfo=tzutc(),
    )
    assert difference.total_seconds() == 0

    difference = parse_ld_date("2022-11-16T15:57:58.123Z") - datetime.datetime(
        2022,
        11,
        16,
        15,
        57,
        58,
        tzinfo=tzutc(),
    )
    assert difference.total_seconds() == 0

    difference = parse_ld_date("2022-12-16T13:32:08+00:00") - datetime.datetime(
        2022,
        12,
        16,
        13,
        32,
        8,
        tzinfo=tzutc(),
    )
    assert difference.total_seconds() == 0


def test_canonicalise_single_attachment():
    data = {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            {
                "schema": "http://schema.org#",
                "PropertyValue": "schema:PropertyValue",
                "value": "schema:value",
            },
        ],
        "attachment": [
            {
                "type": "http://schema.org#PropertyValue",
                "name": "Location",
                "http://schema.org#value": "Test Location",
            },
        ],
    }

    parsed = canonicalise(data)
    attachment = parsed["attachment"]

    assert attachment["type"] == "PropertyValue"
    assert attachment["name"] == "Location"
    assert attachment["value"] == "Test Location"


def test_canonicalise_multiple_attachment():
    data = {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            {
                "schema": "http://schema.org#",
                "PropertyValue": "schema:PropertyValue",
                "value": "schema:value",
            },
        ],
        "attachment": [
            {
                "type": "http://schema.org#PropertyValue",
                "name": "Attachment 1",
                "http://schema.org#value": "Test 1",
            },
            {
                "type": "http://schema.org#PropertyValue",
                "name": "Attachment 2",
                "http://schema.org#value": "Test 2",
            },
        ],
    }

    parsed = canonicalise(data)
    attachment = parsed["attachment"]

    assert len(attachment) == 2

    assert attachment[0]["type"] == "PropertyValue"
    assert attachment[0]["name"] == "Attachment 1"
    assert attachment[0]["value"] == "Test 1"

    assert attachment[1]["type"] == "PropertyValue"
    assert attachment[1]["name"] == "Attachment 2"
    assert attachment[1]["value"] == "Test 2"


def test_get_language():
    assert (
        get_language(
            {
                "contentMap": {
                    "en": "<p>Hello</p>",
                    "es": "<p>hola</p>",
                },
                "nameMap": {"de": "Hallo"},
                "summaryMap": {"fr": "Bonjour"},
            }
        )
        == "en"
    )
    assert (
        get_language(
            {
                "nameMap": {"de": "Hallo"},
                "summaryMap": {"fr": "Bonjour"},
            }
        )
        == "de"
    )
    assert (
        get_language(
            {
                "summaryMap": {"fr": "Bonjour"},
            }
        )
        == "fr"
    )
    assert get_language({"contentMap": {"en-gb": "<p>Hello</p>"}}) == "en"
    assert get_language({"contentMap": {"en_GB": "<p>Hello</p>"}}) == "en"
    assert get_language({"contentMap": {"EN": "<p>Hello</p>"}}) == "en"
    assert get_language({"contentMap": {"und": "<p>Hello</p>"}}) is None
    assert get_language({}) is None


def test_get_first_concrete_type():
    """
    JSON-LD permits "type" to be a list, so we have to cope with both forms
    """
    ACTOR_TYPES = ["person", "service", "application", "group", "organization"]

    assert get_first_concrete_type("Person") == "person"
    assert get_first_concrete_type(["Person"]) == "person"
    # Generic AS base classes lose to a concrete sibling
    assert get_first_concrete_type(["Object", "Note"]) == "note"
    assert get_first_concrete_type(["Activity", "Create"]) == "create"
    # ... but are still returned if that's all we got
    assert get_first_concrete_type(["Collection"]) == "collection"

    # A known type wins regardless of position, so a vocabulary-prefixed
    # duplicate doesn't get stored as the actor type
    assert get_first_concrete_type(["Person", "foaf:Person"], ACTOR_TYPES) == "person"
    assert get_first_concrete_type(["foaf:Person", "Person"], ACTOR_TYPES) == "person"
    # Nothing known: fall back to the first concrete type
    assert get_first_concrete_type(["foaf:Person"], ACTOR_TYPES) == "foaf:person"

    # Junk in, None out
    assert get_first_concrete_type(None) is None
    assert get_first_concrete_type("") is None
    assert get_first_concrete_type([]) is None
    assert get_first_concrete_type([{"id": "Person"}]) is None
    assert get_first_concrete_type({"@value": "Person"}) is None
