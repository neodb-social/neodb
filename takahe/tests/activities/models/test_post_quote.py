import pytest
from activities.models import Post
from core.ld import canonicalise

from users.models import Identity


@pytest.mark.django_db
def test_by_ap_with_fep044f_quote(config_system, remote_identity):
    """FEP-044f canonical `quote` property is parsed."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/quote1",
                "type": "Note",
                "content": "<p>Check this out</p>",
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
                "quote": "https://example.com/posts/original",
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url == "https://example.com/posts/original"


@pytest.mark.django_db
def test_by_ap_with_misskey_quote(config_system, remote_identity):
    """Misskey _misskey_quote property is parsed."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/quote2",
                "type": "Note",
                "content": "<p>Misskey quote</p>",
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
                "_misskey_quote": "https://example.com/posts/misskey-original",
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url == "https://example.com/posts/misskey-original"


@pytest.mark.django_db
def test_by_ap_with_quoteurl(config_system, remote_identity):
    """Legacy quoteUrl property is parsed."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/quote3",
                "type": "Note",
                "content": "<p>Old-style quote</p>",
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
                "quoteUrl": "https://example.com/posts/legacy-original",
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url == "https://example.com/posts/legacy-original"


@pytest.mark.django_db
def test_by_ap_with_quoteuri(config_system, remote_identity):
    """Fedibird quoteUri property is parsed."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/quote4",
                "type": "Note",
                "content": "<p>Fedibird quote</p>",
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
                "quoteUri": "https://example.com/posts/fedibird-original",
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url == "https://example.com/posts/fedibird-original"


@pytest.mark.django_db
def test_by_ap_quote_priority(config_system, remote_identity):
    """FEP-044f `quote` takes priority over `_misskey_quote`."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/quote5",
                "type": "Note",
                "content": "<p>Both properties</p>",
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
                "quote": "https://example.com/posts/fep044f",
                "_misskey_quote": "https://example.com/posts/misskey",
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url == "https://example.com/posts/fep044f"


@pytest.mark.django_db
def test_by_ap_tombstone_quote(config_system, remote_identity):
    """Tombstone quote (deleted) is not stored."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/quote6",
                "type": "Note",
                "content": "<p>Deleted quote</p>",
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
                "quote": {"type": "Tombstone"},
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url is None


@pytest.mark.django_db
def test_by_ap_fep_e232_tag_link(config_system, remote_identity):
    """FEP-e232 tag Link is used as fallback."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/quote7",
                "type": "Note",
                "content": "<p>Tag link quote</p>",
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
                "tag": [
                    {
                        "type": "Link",
                        "mediaType": 'application/ld+json; profile="https://www.w3.org/ns/activitystreams"',
                        "href": "https://example.com/posts/tag-link-original",
                    }
                ],
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url == "https://example.com/posts/tag-link-original"


@pytest.mark.django_db
def test_by_ap_no_quote(config_system, remote_identity):
    """Post without quote has quote_url=None."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/noquote",
                "type": "Note",
                "content": "<p>Normal post</p>",
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url is None


@pytest.mark.django_db
def test_by_ap_strips_quote_inline_span(config_system, remote_identity):
    """FEP-044f fallback quote-inline span is stripped from content."""
    post = Post.by_ap(
        data=canonicalise(
            {
                "id": "https://remote.test/posts/quote-strip",
                "type": "Note",
                "content": '<p>Check this out</p><span class="quote-inline"><br/>RE: <a href="https://example.com/posts/original">https://example.com/posts/original</a></span>',
                "attributedTo": remote_identity.actor_uri,
                "published": "2024-01-01T00:00:00Z",
                "quote": "https://example.com/posts/original",
            },
            include_security=True,
        ),
        create=True,
    )
    assert post.quote_url == "https://example.com/posts/original"
    assert "quote-inline" not in post.content


@pytest.mark.django_db
def test_to_ap_with_quote(identity: Identity, config_system):
    """Quote post serializes all compat properties."""
    post = Post.create_local(author=identity, content="Quoting this")
    post.quote_url = "https://remote.test/posts/original"
    post.save()
    ap = post.to_ap()
    assert ap["quote"] == "https://remote.test/posts/original"
    assert ap["quoteUrl"] == "https://remote.test/posts/original"
    assert ap["quoteUri"] == "https://remote.test/posts/original"
    assert ap["_misskey_quote"] == "https://remote.test/posts/original"
    # FEP-e232 tag link
    tag_links = [t for t in ap.get("tag", []) if t.get("type") == "Link"]
    assert any(t["href"] == "https://remote.test/posts/original" for t in tag_links)


@pytest.mark.django_db
def test_to_ap_no_quote_properties_without_quote(identity: Identity, config_system):
    """Non-quote posts do not include quote properties."""
    post = Post.create_local(author=identity, content="Normal post")
    ap = post.to_ap()
    assert "quote" not in ap
    assert "quoteUrl" not in ap
    assert "_misskey_quote" not in ap


@pytest.mark.django_db
def test_to_ap_interaction_policy(identity: Identity, config_system):
    """Public posts advertise interactionPolicy.canQuote."""
    post = Post.create_local(
        author=identity, content="Public post", visibility=Post.Visibilities.public
    )
    ap = post.to_ap()
    assert "interactionPolicy" in ap
    assert "canQuote" in ap["interactionPolicy"]
    assert "automaticApproval" in ap["interactionPolicy"]["canQuote"]


@pytest.mark.django_db
def test_create_local_with_quote(identity: Identity, config_system):
    """create_local sets quote_url from quote param."""
    original = Post.create_local(author=identity, content="Original")
    quote = Post.create_local(author=identity, content="My quote", quote=original)
    assert quote.quote_url == original.object_uri


@pytest.mark.django_db
def test_mastodon_json_with_quote(identity: Identity, config_system):
    """Mastodon JSON includes quoted_status."""
    original = Post.create_local(author=identity, content="Original post")
    quote = Post.create_local(author=identity, content="My quote")
    quote.quote_url = original.object_uri
    quote.save()
    mastodon = quote.to_mastodon_json()
    assert mastodon["quote"] is not None
    assert mastodon["quote"]["state"] == "accepted"
    assert "quoted_status" in mastodon["quote"]


@pytest.mark.django_db
def test_mastodon_json_no_quote(identity: Identity, config_system):
    """Mastodon JSON has null quote when not quoting."""
    post = Post.create_local(author=identity, content="Normal post")
    mastodon = post.to_mastodon_json()
    assert mastodon["quote"] is None


@pytest.mark.django_db
def test_get_targets_includes_quoted_author(
    identity: Identity, other_identity: Identity, config_system
):
    """Fan-out targets include the author of the quoted post."""
    original = Post.create_local(author=other_identity, content="Original")
    quote = Post.create_local(author=identity, content="My quote", quote=original)
    targets = set(quote.get_targets())
    assert other_identity in targets
