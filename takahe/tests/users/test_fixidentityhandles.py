from io import StringIO

import pytest
from activities.models import Post
from django.core.management import call_command
from users.models import Domain, Identity


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
    assert not Identity.objects.filter(pk=alias.pk).exists()
    post.refresh_from_db()
    assert post.author_id == canonical.pk
    canonical.refresh_from_db()
    assert canonical.state == "outdated"


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
    assert not Identity.objects.filter(pk=alias.pk).exists()
    post.refresh_from_db()
    assert post.author_id == canonical.pk


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
