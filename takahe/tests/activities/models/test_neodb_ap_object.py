"""Tests for NeoDB-specific AP object handling in Takahe Post.

Verifies that the relatedWith/tag extensions used by NeoDB journal entries
are:
1. Correctly included when serializing outgoing AP JSON (to_ap / to_create_ap)
2. Correctly stored when parsing incoming AP JSON that contains relatedWith
   (by_ap) so that no data is stripped or changed
3. Exposed via the ext_neodb field in the Mastodon-compatible API

These fields carry journal piece metadata (ShelfMember Status, Review, Rating,
Note, Comment) between NeoDB instances.
"""

import pytest

from activities.models import Post
from users.models import Identity


# ---------------------------------------------------------------------------
# Sample AP objects used across tests
# ---------------------------------------------------------------------------

_ITEM_URL = "https://neodb.example.com/books/01ABC"

_STATUS_AP = {
    "id": "https://neodb.example.com/~/shelf/01ABC",
    "type": "Status",
    "status": "complete",
    "published": "2024-01-01T00:00:00+00:00",
    "updated": "2024-01-01T00:00:00+00:00",
    "attributedTo": "https://remote.test/test-actor/",
    "withRegardTo": _ITEM_URL,
    "href": "https://neodb.example.com/~/shelf/01ABC",
}

_COMMENT_AP = {
    "id": "https://neodb.example.com/~/comment/01ABC",
    "type": "Comment",
    "content": "Really good read",
    "published": "2024-01-01T00:00:00+00:00",
    "updated": "2024-01-01T00:00:00+00:00",
    "attributedTo": "https://remote.test/test-actor/",
    "withRegardTo": _ITEM_URL,
    "href": "https://neodb.example.com/~/comment/01ABC",
}

_RATING_AP = {
    "id": "https://neodb.example.com/~/rating/01ABC",
    "type": "Rating",
    "best": 10,
    "worst": 1,
    "value": 8,
    "published": "2024-01-01T00:00:00+00:00",
    "updated": "2024-01-01T00:00:00+00:00",
    "attributedTo": "https://remote.test/test-actor/",
    "withRegardTo": _ITEM_URL,
    "href": "https://neodb.example.com/~/rating/01ABC",
}

_REVIEW_AP = {
    "id": "https://neodb.example.com/~/review/01ABC",
    "type": "Review",
    "name": "My Review Title",
    "content": "A fantastic book.",
    "mediaType": "text/markdown",
    "published": "2024-01-01T00:00:00+00:00",
    "updated": "2024-01-01T00:00:00+00:00",
    "attributedTo": "https://remote.test/test-actor/",
    "withRegardTo": _ITEM_URL,
    "href": "https://neodb.example.com/~/review/01ABC",
}

_NOTE_AP = {
    "id": "https://neodb.example.com/~/note/01ABC",
    "type": "Note",
    "title": "Reading notes",
    "content": "At chapter 5",
    "sensitive": False,
    "progress": {"type": "chapter", "value": "5"},
    "published": "2024-01-01T00:00:00+00:00",
    "updated": "2024-01-01T00:00:00+00:00",
    "attributedTo": "https://remote.test/test-actor/",
    "withRegardTo": _ITEM_URL,
    "href": "https://neodb.example.com/~/note/01ABC",
}

_ITEM_TAG = {
    "type": "Link",
    "href": _ITEM_URL,
    "mediaType": "application/activity+json",
}


def _make_incoming_ap(
    remote_identity: Identity,
    related_with: list,
    extra_tag: list | None = None,
    uri_suffix: str = "test-post",
) -> dict:
    """Build a minimal AP Note object as NeoDB sends it for journal entries."""
    obj: dict = {
        "id": f"https://remote.test/test-actor/{uri_suffix}",
        "type": "Note",
        "published": "2024-01-01T00:00:00+00:00",
        "attributedTo": remote_identity.actor_uri,
        "content": "<p>marked a book</p>",
        "relatedWith": related_with,
    }
    tag = list(extra_tag or [])
    obj["tag"] = tag
    return obj


# ===========================================================================
# Outgoing: to_ap / to_create_ap
# ===========================================================================


@pytest.mark.django_db
def test_to_ap_includes_related_with(identity: Identity, config_system):
    """to_ap() merges type_data['object'] so relatedWith appears in output."""
    post = Post.create_local(author=identity, content="<p>marked a book</p>")
    post.type_data = {
        "object": {
            "tag": [_ITEM_TAG],
            "relatedWith": [_STATUS_AP],
        }
    }
    post.save()

    ap = post.to_ap()
    assert "relatedWith" in ap
    assert ap["relatedWith"] == [_STATUS_AP]


@pytest.mark.django_db
def test_to_ap_includes_multiple_related_with(identity: Identity, config_system):
    """All objects (Status + Comment + Rating) appear in the relatedWith list."""
    post = Post.create_local(author=identity, content="<p>marked a book</p>")
    post.type_data = {
        "object": {
            "tag": [_ITEM_TAG],
            "relatedWith": [_STATUS_AP, _COMMENT_AP, _RATING_AP],
        }
    }
    post.save()

    ap = post.to_ap()
    assert "relatedWith" in ap
    related_types = {obj["type"] for obj in ap["relatedWith"]}
    assert related_types == {"Status", "Comment", "Rating"}
    rating = next(o for o in ap["relatedWith"] if o["type"] == "Rating")
    assert rating["value"] == 8


@pytest.mark.django_db
def test_to_ap_merges_item_tag_with_hashtags(identity: Identity, config_system):
    """type_data tag (item reference) is merged with hashtag tags from the post."""
    post = Post.create_local(author=identity, content="<p>marked #scifi</p>")
    post.type_data = {
        "object": {
            "tag": [_ITEM_TAG],
            "relatedWith": [_STATUS_AP],
        }
    }
    post.save()

    ap = post.to_ap()
    tag_types = {t.get("type") for t in ap["tag"]}
    # both the item Link tag and the Hashtag tag must be present
    assert "Link" in tag_types
    assert "Hashtag" in tag_types
    link_tags = [t for t in ap["tag"] if t.get("type") == "Link"]
    assert any(t.get("href") == _ITEM_URL for t in link_tags)


@pytest.mark.django_db
def test_to_ap_without_type_data_has_no_related_with(identity: Identity, config_system):
    """A plain post with no type_data must not have relatedWith in its AP JSON."""
    post = Post.create_local(author=identity, content="<p>just a note</p>")

    ap = post.to_ap()
    assert "relatedWith" not in ap


@pytest.mark.django_db
def test_to_create_ap_propagates_related_with(identity: Identity, config_system):
    """to_create_ap() wraps to_ap(), so relatedWith must survive in the object."""
    post = Post.create_local(author=identity, content="<p>marked a book</p>")
    post.type_data = {
        "object": {
            "tag": [_ITEM_TAG],
            "relatedWith": [_STATUS_AP],
        }
    }
    post.save()

    create_ap = post.to_create_ap()
    assert create_ap["type"] == "Create"
    assert "relatedWith" in create_ap["object"]
    assert create_ap["object"]["relatedWith"] == [_STATUS_AP]


@pytest.mark.django_db
def test_to_ap_review_related_with(identity: Identity, config_system):
    """Review ap_object is preserved intact through to_ap()."""
    post = Post.create_local(author=identity, content="<p>reviewed a book</p>")
    post.type_data = {
        "object": {
            "tag": [_ITEM_TAG],
            "relatedWith": [_REVIEW_AP],
        }
    }
    post.save()

    ap = post.to_ap()
    assert "relatedWith" in ap
    review = ap["relatedWith"][0]
    assert review["type"] == "Review"
    assert review["name"] == "My Review Title"
    assert review["content"] == "A fantastic book."
    assert review["mediaType"] == "text/markdown"


@pytest.mark.django_db
def test_to_ap_note_related_with_with_progress(identity: Identity, config_system):
    """Note ap_object including progress field is preserved intact through to_ap()."""
    post = Post.create_local(author=identity, content="<p>reading note</p>")
    post.type_data = {
        "object": {
            "tag": [_ITEM_TAG],
            "relatedWith": [_NOTE_AP],
        }
    }
    post.save()

    ap = post.to_ap()
    assert "relatedWith" in ap
    note = ap["relatedWith"][0]
    assert note["type"] == "Note"
    assert note["title"] == "Reading notes"
    assert note["progress"]["type"] == "chapter"
    assert note["progress"]["value"] == "5"


# ===========================================================================
# Incoming: by_ap
# ===========================================================================


@pytest.mark.django_db
def test_by_ap_with_related_with_stores_object_in_type_data(
    remote_identity: Identity,
):
    """Incoming AP with relatedWith stores the full AP object in type_data['object']."""
    data = _make_incoming_ap(remote_identity, [_STATUS_AP])

    post = Post.by_ap(data=data, create=True)

    assert isinstance(post.type_data, dict)
    assert "object" in post.type_data
    obj = post.type_data["object"]
    assert "relatedWith" in obj
    assert obj["relatedWith"] == [_STATUS_AP]


@pytest.mark.django_db
def test_by_ap_with_multiple_related_with_preserves_all(
    remote_identity: Identity,
):
    """All relatedWith objects (Status, Comment, Rating) are preserved in type_data."""
    data = _make_incoming_ap(
        remote_identity,
        [_STATUS_AP, _COMMENT_AP, _RATING_AP],
        uri_suffix="test-post-multi",
    )

    post = Post.by_ap(data=data, create=True)

    related = post.type_data["object"]["relatedWith"]
    assert len(related) == 3
    types = {o["type"] for o in related}
    assert types == {"Status", "Comment", "Rating"}

    status = next(o for o in related if o["type"] == "Status")
    assert status["status"] == "complete"

    comment = next(o for o in related if o["type"] == "Comment")
    assert comment["content"] == "Really good read"

    rating = next(o for o in related if o["type"] == "Rating")
    assert rating["value"] == 8
    assert rating["best"] == 10
    assert rating["worst"] == 1


@pytest.mark.django_db
def test_by_ap_with_review_related_with(remote_identity: Identity):
    """Review ap_object in relatedWith is preserved in type_data."""
    data = _make_incoming_ap(
        remote_identity, [_REVIEW_AP], uri_suffix="test-post-review"
    )

    post = Post.by_ap(data=data, create=True)

    related = post.type_data["object"]["relatedWith"]
    assert len(related) == 1
    review = related[0]
    assert review["type"] == "Review"
    assert review["name"] == "My Review Title"
    assert review["content"] == "A fantastic book."
    assert review["mediaType"] == "text/markdown"


@pytest.mark.django_db
def test_by_ap_with_note_related_with_preserves_progress(remote_identity: Identity):
    """Note ap_object with progress field is fully preserved in type_data."""
    data = _make_incoming_ap(remote_identity, [_NOTE_AP], uri_suffix="test-post-note")

    post = Post.by_ap(data=data, create=True)

    related = post.type_data["object"]["relatedWith"]
    note = related[0]
    assert note["type"] == "Note"
    assert note["title"] == "Reading notes"
    assert note["progress"]["type"] == "chapter"
    assert note["progress"]["value"] == "5"


@pytest.mark.django_db
def test_by_ap_without_related_with_leaves_type_data_alone(
    remote_identity: Identity,
):
    """A plain incoming AP Note without relatedWith must NOT create a dict type_data."""
    data = {
        "id": "https://remote.test/test-actor/test-plain-post",
        "type": "Note",
        "published": "2024-01-01T00:00:00+00:00",
        "attributedTo": remote_identity.actor_uri,
        "content": "<p>just a plain post</p>",
    }

    post = Post.by_ap(data=data, create=True)

    # type_data may be None or a non-dict (e.g. QuestionData) but never a
    # dict containing an "object" key with relatedWith
    assert not (
        isinstance(post.type_data, dict)
        and "relatedWith" in post.type_data.get("object", {})
    )


@pytest.mark.django_db
def test_by_ap_update_preserves_related_with(remote_identity: Identity):
    """Updating an existing post via by_ap keeps relatedWith in type_data."""
    data = _make_incoming_ap(
        remote_identity, [_STATUS_AP], uri_suffix="test-post-update"
    )
    post = Post.by_ap(data=data, create=True)
    assert "relatedWith" in post.type_data["object"]

    # Now simulate an update with a new status
    updated_status = dict(_STATUS_AP)
    updated_status["updated"] = "2024-06-01T00:00:00+00:00"
    updated_data = _make_incoming_ap(
        remote_identity, [updated_status], uri_suffix="test-post-update"
    )
    updated_data["updated"] = "2024-06-01T00:00:00+00:00"
    updated_post = Post.by_ap(data=updated_data, create=False, update=True)

    assert isinstance(updated_post.type_data, dict)
    assert "relatedWith" in updated_post.type_data["object"]
    related = updated_post.type_data["object"]["relatedWith"]
    assert related[0]["updated"] == "2024-06-01T00:00:00+00:00"


@pytest.mark.django_db
def test_by_ap_preserves_item_tag(remote_identity: Identity):
    """The tag list (including the item reference Link) is preserved in type_data."""
    data = _make_incoming_ap(
        remote_identity,
        [_STATUS_AP],
        extra_tag=[_ITEM_TAG],
        uri_suffix="test-post-tag",
    )

    post = Post.by_ap(data=data, create=True)

    obj = post.type_data["object"]
    assert "tag" in obj
    link_tags = [t for t in obj["tag"] if t.get("type") == "Link"]
    assert any(t.get("href") == _ITEM_URL for t in link_tags)


# ===========================================================================
# Mastodon API: to_mastodon_json
# ===========================================================================


@pytest.mark.django_db
def test_to_mastodon_json_exposes_ext_neodb(identity: Identity, config_system):
    """When type_data has 'object', to_mastodon_json exposes it as ext_neodb."""
    post = Post.create_local(author=identity, content="<p>marked a book</p>")
    post.type_data = {
        "object": {
            "tag": [_ITEM_TAG],
            "relatedWith": [_STATUS_AP],
        }
    }
    post.save()

    json_out = post.to_mastodon_json(identity=identity)
    assert "ext_neodb" in json_out
    assert "relatedWith" in json_out["ext_neodb"]
    assert json_out["ext_neodb"]["relatedWith"] == [_STATUS_AP]


@pytest.mark.django_db
def test_to_mastodon_json_no_ext_neodb_without_type_data(
    identity: Identity, config_system
):
    """A plain post without NeoDB type_data must not expose ext_neodb."""
    post = Post.create_local(author=identity, content="<p>just a note</p>")

    json_out = post.to_mastodon_json(identity=identity)
    assert "ext_neodb" not in json_out


@pytest.mark.django_db
def test_to_mastodon_json_ext_neodb_includes_all_related_with(
    identity: Identity, config_system
):
    """All relatedWith objects are exposed intact through ext_neodb."""
    post = Post.create_local(author=identity, content="<p>marked a book</p>")
    post.type_data = {
        "object": {
            "tag": [_ITEM_TAG],
            "relatedWith": [_STATUS_AP, _RATING_AP],
        }
    }
    post.save()

    json_out = post.to_mastodon_json(identity=identity)
    related = json_out["ext_neodb"]["relatedWith"]
    assert len(related) == 2
    types = {o["type"] for o in related}
    assert types == {"Status", "Rating"}
