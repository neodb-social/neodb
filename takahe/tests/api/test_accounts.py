import pytest


@pytest.mark.django_db
def test_verify_credentials(api_client, identity):
    response = api_client.get("/api/v1/accounts/verify_credentials").json()
    assert response["id"] == str(identity.pk)
    assert response["username"] == identity.username


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
