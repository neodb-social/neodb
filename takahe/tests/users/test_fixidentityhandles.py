from io import StringIO

import pytest
from activities.models import Conversation, Post
from core.models import Config
from django.core.management import call_command
from users.models import Domain, Follow, Identity


def actor_document(actor_uri: str, document_id: str, username: str) -> dict:
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": document_id,
        "type": "Person",
        "preferredUsername": username,
        "inbox": f"{document_id}/inbox",
    }


def mock_actor(httpx_mock, actor_uri: str, document_id: str, username: str):
    httpx_mock.add_response(
        url=actor_uri,
        headers={"Content-Type": "application/activity+json"},
        json=actor_document(actor_uri, document_id, username),
        is_reusable=True,
    )


def run(**kwargs) -> str:
    out = StringIO()
    call_command("fixidentityhandles", stdout=out, **kwargs)
    return out.getvalue()


@pytest.fixture
def _no_federation(settings):
    original = settings.SETUP.NO_FEDERATION
    settings.SETUP.NO_FEDERATION = False
    yield
    settings.SETUP.NO_FEDERATION = original


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_releasable_alias_gives_the_handle_back(
    httpx_mock, config_system, _no_federation
):
    """
    The handle is held by a row whose document proves it is an alias of the
    stuck one, which is how friendship.quest/ruben lost its handle and its
    posts' author.
    """
    domain = Domain.get_remote_domain("example.com")
    canonical = Identity.objects.create(
        actor_uri="https://example.com/ruben",
        local=False,
    )
    alias = Identity.objects.create(
        actor_uri="https://example.com/users/ruben",
        username="ruben",
        domain=domain,
        local=False,
    )
    post = Post.objects.create(author=alias, local=False, content="<p>hi</p>")
    mock_actor(httpx_mock, canonical.actor_uri, canonical.actor_uri, "ruben")
    mock_actor(httpx_mock, alias.actor_uri, canonical.actor_uri, "ruben")

    output = run(fix=True, yes=True)

    assert "releasable" in output
    post.refresh_from_db()
    assert post.author_id == canonical.pk
    canonical.refresh_from_db()
    assert canonical.state == "outdated"
    # Emptied, not deleted: NeoDB mirrors identities by primary key from
    # another database, and a review owned by a row deleted here would point
    # at nothing
    alias.refresh_from_db()
    assert alias.username is None
    assert alias.domain_id is None
    # And peers still addressing the actor by the alias URI resolve to the
    # identity that now holds everything
    assert alias.canonical_id == canonical.pk
    assert Identity.by_actor_uri(alias.actor_uri).pk == canonical.pk


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_scan_only_by_default(httpx_mock, config_system, _no_federation):
    domain = Domain.get_remote_domain("example.com")
    canonical = Identity.objects.create(
        actor_uri="https://example.com/ruben",
        local=False,
    )
    alias = Identity.objects.create(
        actor_uri="https://example.com/users/ruben",
        username="ruben",
        domain=domain,
        local=False,
    )
    mock_actor(httpx_mock, canonical.actor_uri, canonical.actor_uri, "ruben")
    mock_actor(httpx_mock, alias.actor_uri, canonical.actor_uri, "ruben")

    output = run()

    assert "1 repairable" in output
    assert Identity.objects.filter(pk=alias.pk).exists()
    alias.refresh_from_db()
    assert alias.username == "ruben"


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_row_that_is_itself_an_alias_merges_into_the_actor_it_names(
    httpx_mock, config_system, _no_federation
):
    """
    The stuck row can be the alias instead, as musician.social/@mirlo is. Its
    rows belong to the actor its document names.
    """
    canonical = Identity.objects.create(
        actor_uri="https://example.com/users/mirlo",
        local=False,
    )
    alias = Identity.objects.create(
        actor_uri="https://example.com/@mirlo",
        local=False,
    )
    post = Post.objects.create(author=alias, local=False, content="<p>hi</p>")
    mock_actor(httpx_mock, alias.actor_uri, canonical.actor_uri, "mirlo")
    mock_actor(httpx_mock, canonical.actor_uri, canonical.actor_uri, "mirlo")

    output = run(fix=True, yes=True)

    assert "alias" in output
    post.refresh_from_db()
    assert post.author_id == canonical.pk
    alias.refresh_from_db()
    assert alias.username is None


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_two_distinct_actors_are_left_alone(httpx_mock, config_system, _no_federation):
    """
    A Lemmy user and a community of the same name both want books@lemmy, and
    the schema cannot hold both. Nothing here can repair that, so nothing may
    touch it either.
    """
    domain = Domain.get_remote_domain("lemmy.example")
    community = Identity.objects.create(
        actor_uri="https://lemmy.example/c/books",
        username="books",
        domain=domain,
        local=False,
    )
    user = Identity.objects.create(
        actor_uri="https://lemmy.example/u/books",
        local=False,
    )
    mock_actor(httpx_mock, user.actor_uri, user.actor_uri, "books")
    mock_actor(httpx_mock, community.actor_uri, community.actor_uri, "books")

    output = run(fix=True, yes=True)

    assert "unfixable" in output
    assert Identity.objects.filter(pk=community.pk).exists()
    assert Identity.objects.filter(pk=user.pk).exists()
    user.refresh_from_db()
    assert user.username is None


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_free_handle_is_only_refetched(httpx_mock, config_system, _no_federation):
    identity = Identity.objects.create(
        actor_uri="https://example.com/users/nobody",
        local=False,
        state="updated",
    )
    mock_actor(httpx_mock, identity.actor_uri, identity.actor_uri, "nobody")

    output = run(fix=True, yes=True)

    assert "free" in output
    identity.refresh_from_db()
    assert identity.state == "outdated"
    assert identity.username is None


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_unreachable_actor_is_left_alone(httpx_mock, config_system, _no_federation):
    identity = Identity.objects.create(
        actor_uri="https://example.com/users/gone",
        local=False,
        state="updated",
    )
    httpx_mock.add_response(url=identity.actor_uri, status_code=404, is_reusable=True)

    output = run(fix=True, yes=True)

    assert "unreachable" in output
    identity.refresh_from_db()
    assert identity.state == "updated"


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_cross_host_claim_is_never_merged(httpx_mock, config_system, _no_federation):
    """
    Only the server an actor lives on may call it an alias of another. A
    document naming a different host's actor is how a server would take that
    actor's posts, and a genuine move (bae.st to shitpost.cloud) is
    indistinguishable from it, so both are reported instead.
    """
    canonical = Identity.objects.create(
        actor_uri="https://other.example/users/owl",
        local=False,
    )
    claimer = Identity.objects.create(
        actor_uri="https://claimer.example/users/owl",
        local=False,
    )
    post = Post.objects.create(author=claimer, local=False, content="<p>mine</p>")
    mock_actor(httpx_mock, claimer.actor_uri, canonical.actor_uri, "owl")
    mock_actor(httpx_mock, canonical.actor_uri, canonical.actor_uri, "owl")

    output = run(fix=True, yes=True)

    assert "foreign" in output
    post.refresh_from_db()
    assert post.author_id == claimer.pk


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_merge_rewrites_the_conversation_key(httpx_mock, config_system, _no_federation):
    """
    A conversation is found by a hash of its participants, so moving one
    without rewriting the hash hides the thread from everything that looks it
    up and splits the history in two.
    """
    domain = Domain.get_remote_domain("example.com")
    canonical = Identity.objects.create(
        actor_uri="https://example.com/ruben",
        local=False,
    )
    alias = Identity.objects.create(
        actor_uri="https://example.com/users/ruben",
        username="ruben",
        domain=domain,
        local=False,
    )
    other = Identity.objects.create(
        actor_uri="https://example.com/someone",
        username="someone",
        domain=domain,
        local=False,
    )
    conversation = Conversation.get_or_create_for_participants({alias.pk, other.pk})
    mock_actor(httpx_mock, canonical.actor_uri, canonical.actor_uri, "ruben")
    mock_actor(httpx_mock, alias.actor_uri, canonical.actor_uri, "ruben")

    run(fix=True, yes=True)

    conversation.refresh_from_db()
    assert conversation.participant_hash == Conversation.compute_participant_hash(
        {canonical.pk, other.pk}
    )
    assert (
        Conversation.get_or_create_for_participants({canonical.pk, other.pk}).pk
        == conversation.pk
    )


@pytest.mark.django_db
def test_loads_the_system_config():
    """
    Only middleware and the stator runner load it, and SystemActor reads it to
    sign a probe, so a management process that skips this cannot fetch a
    single actor. Deliberately runs without the config_system fixture, which
    is what hid this.
    """
    Config.__forced__ = False
    if hasattr(Config, "system"):
        del Config.system

    run()

    assert getattr(Config, "system", None) is not None


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_restricted_alias_is_not_merged(httpx_mock, config_system, _no_federation):
    """
    Moving a limited or blocked identity's posts onto an unrestricted one
    would quietly undo a moderator's decision, because the restriction lives
    on the identity and cannot move with them.
    """
    domain = Domain.get_remote_domain("example.com")
    canonical = Identity.objects.create(
        actor_uri="https://example.com/ruben",
        local=False,
    )
    alias = Identity.objects.create(
        actor_uri="https://example.com/users/ruben",
        username="ruben",
        domain=domain,
        local=False,
        restriction=Identity.Restriction.blocked,
    )
    post = Post.objects.create(author=alias, local=False, content="<p>hi</p>")
    mock_actor(httpx_mock, canonical.actor_uri, canonical.actor_uri, "ruben")
    mock_actor(httpx_mock, alias.actor_uri, canonical.actor_uri, "ruben")

    output = run(fix=True, yes=True)

    assert "skipped" in output
    post.refresh_from_db()
    assert post.author_id == alias.pk
    alias.refresh_from_db()
    assert alias.username == "ruben"


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_clashing_row_aborts_the_whole_merge(httpx_mock, config_system, _no_federation):
    """
    Where only one row can exist, the two are not necessarily the same: two
    follows hold different states. Nothing here can choose between them, so
    the merge rolls back rather than throw one away.
    """
    domain = Domain.get_remote_domain("example.com")
    canonical = Identity.objects.create(
        actor_uri="https://example.com/ruben",
        local=False,
    )
    alias = Identity.objects.create(
        actor_uri="https://example.com/users/ruben",
        username="ruben",
        domain=domain,
        local=False,
    )
    target = Identity.objects.create(
        actor_uri="https://example.com/target",
        username="target",
        domain=domain,
        local=False,
    )
    Follow.objects.create(source=alias, target=target, state="unrequested")
    Follow.objects.create(source=canonical, target=target, state="accepted")
    post = Post.objects.create(author=alias, local=False, content="<p>hi</p>")
    mock_actor(httpx_mock, canonical.actor_uri, canonical.actor_uri, "ruben")
    mock_actor(httpx_mock, alias.actor_uri, canonical.actor_uri, "ruben")

    output = run(fix=True, yes=True)

    assert "skipped" in output
    # Nothing moved, and both follows are still there
    post.refresh_from_db()
    assert post.author_id == alias.pk
    assert Follow.objects.filter(source=alias, target=target).exists()
    assert Follow.objects.filter(source=canonical, target=target).exists()


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_merge_follows_a_target_merged_earlier_in_the_batch(
    httpx_mock, config_system, _no_federation
):
    """
    A whole batch is classified before any of it is repaired, so the target
    can be merged away in between. by_actor_uri takes one hop, so a chain
    would leave the actor resolving to an emptied row.
    """
    domain = Domain.get_remote_domain("example.com")
    final = Identity.objects.create(
        actor_uri="https://example.com/ruben",
        local=False,
    )
    middle = Identity.objects.create(
        actor_uri="https://example.com/users/ruben",
        username="ruben",
        domain=domain,
        local=False,
    )
    first = Identity.objects.create(
        actor_uri="https://example.com/@ruben",
        local=False,
    )
    post = Post.objects.create(author=first, local=False, content="<p>hi</p>")
    mock_actor(httpx_mock, final.actor_uri, final.actor_uri, "ruben")
    mock_actor(httpx_mock, middle.actor_uri, final.actor_uri, "ruben")
    mock_actor(httpx_mock, first.actor_uri, middle.actor_uri, "ruben")

    run(fix=True, yes=True)

    # Whichever order they were repaired in, every URI reaches the one
    # identity, in a single hop
    first.refresh_from_db()
    middle.refresh_from_db()
    assert first.canonical_id == final.pk
    assert middle.canonical_id == final.pk
    assert Identity.by_actor_uri(first.actor_uri).pk == final.pk
    assert Identity.by_actor_uri(middle.actor_uri).pk == final.pk
    post.refresh_from_db()
    assert post.author_id == final.pk
