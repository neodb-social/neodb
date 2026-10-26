from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import RequestFactory

from common.api import OAuthAccessTokenAuth
from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _token_for(user: User) -> SimpleNamespace:
    return SimpleNamespace(
        revoked=None,
        scopes=["read", "write"],
        identity_id=user.identity.pk,
        application_id=1,
    )


def test_deactivated_user_token_is_rejected() -> None:
    user = User.register(email="inactive@example.com", username="inactive")
    request = RequestFactory().get("/api/me")

    with mock.patch.object(Takahe, "get_token", return_value=_token_for(user)):
        assert OAuthAccessTokenAuth().authenticate(request, "tok") is True
        user.is_active = False
        user.save(update_fields=["is_active"])
        assert OAuthAccessTokenAuth().authenticate(request, "tok") is False
