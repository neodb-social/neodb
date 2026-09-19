import json

import pytest


@pytest.mark.django_db
def test_verify_credentials(api_client, identity):
    response = api_client.get("/api/v1/accounts/verify_credentials").json()
    assert response["id"] == str(identity.pk)
    assert response["username"] == identity.username


@pytest.mark.django_db
def test_update_credentials_privacy_reflected_immediately(api_client, identity):
    """The update_credentials response must echo the privacy just written, not a
    stale value cached by ConfigLoadingMiddleware via the config_identity
    cached_property (Config.set_identity invalidates that cache)."""
    response = api_client.patch(
        "/api/v1/accounts/update_credentials",
        data=json.dumps({"source": {"privacy": "private"}}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["source"]["privacy"] == "private"


@pytest.mark.django_db
def test_account_search(api_client, identity):
    response = api_client.get("/api/v1/accounts/search?q=test").json()
    assert response[0]["id"] == str(identity.pk)
    assert response[0]["username"] == identity.username


@pytest.mark.django_db
def test_following_count_consistent_after_unfollow(
    api_client, identity, other_identity
):
    """#1616: Account API following_count must reflect follow state changes."""
    response = api_client.post(
        f"/api/v1/accounts/{other_identity.pk}/follow",
        content_type="application/json",
    )
    assert response.status_code == 200
    own = api_client.get("/api/v1/accounts/verify_credentials").json()
    assert own["following_count"] == 1

    response = api_client.post(
        f"/api/v1/accounts/{other_identity.pk}/unfollow",
        content_type="application/json",
    )
    assert response.status_code == 200
    own = api_client.get("/api/v1/accounts/verify_credentials").json()
    assert own["following_count"] == 0


@pytest.mark.django_db
def test_account_action_on_a_merged_id_reaches_the_real_identity(
    api_client, identity, remote_identity
):
    """
    A client keeps the numeric id it was given, which may be one a merge has
    since emptied. Blocking that row would report success while the actor
    went on being seen.
    """
    from users.models import Block, Identity

    alias = Identity.objects.create(
        actor_uri="https://remote.test/users/test/",
        local=False,
        canonical=remote_identity,
    )

    response = api_client.post(
        f"/api/v1/accounts/{alias.pk}/block",
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(remote_identity.pk)
    assert Block.objects.filter(
        source=identity, target=remote_identity, mute=False
    ).exists()
    assert not Block.objects.filter(target=alias).exists()


@pytest.mark.django_db
def test_a_merged_id_cannot_reach_a_blocked_identity(
    api_client, identity, remote_identity
):
    """
    The moderation check belongs to the identity a request ends at. An alias
    is a row nobody moderated, so checking it and then resolving would serve
    the account behind it however it was restricted.
    """
    from users.models import Identity

    remote_identity.restriction = Identity.Restriction.blocked
    remote_identity.save()
    alias = Identity.objects.create(
        actor_uri="https://remote.test/users/test/",
        local=False,
        canonical=remote_identity,
    )

    assert api_client.get(f"/api/v1/accounts/{alias.pk}").status_code == 404
    assert (
        api_client.post(
            f"/api/v1/accounts/{alias.pk}/follow", content_type="application/json"
        ).status_code
        == 404
    )
