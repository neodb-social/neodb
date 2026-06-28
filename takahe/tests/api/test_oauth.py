import pytest


@pytest.mark.django_db
def test_authorize_missing_redirect_uri(client_with_user):
    """
    Hitting /oauth/authorize without a redirect_uri must return a graceful 400
    error page naming the missing param rather than raising
    MultiValueDictKeyError (NEODB-SOCIAL-7NR). response_type is supplied so the
    400 can only originate from the redirect_uri guard, not a later check.
    """
    response = client_with_user.get("/oauth/authorize?response_type=code")
    assert response.status_code == 400
    assert "Missing redirect_uri" in response.content.decode()
