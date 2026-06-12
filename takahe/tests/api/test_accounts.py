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
