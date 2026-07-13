import pytest
from django.test import Client
from django.urls import reverse

from takahe.models import PushNotification, PushSubscription, Token
from takahe.utils import Takahe
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestRevokeToken:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="revoke@test.com", username="revoker")
        self.identity = self.user.identity
        self.token = Takahe.create_personal_token(
            self.identity.pk, self.user.pk, "test app", "write"
        )

    def test_revoke_token_cascades_push_rows(self):
        PushSubscription.objects.create(
            token=self.token, endpoint="https://push.example.com/x"
        )
        PushNotification.objects.create(
            token=self.token, type="mention", icon="", title="t", body="b"
        )
        assert Takahe.revoke_token(self.token.pk, self.identity.pk)
        assert not Token.objects.filter(pk=self.token.pk).exists()
        assert not PushNotification.objects.filter(token_id=self.token.pk).exists()
        assert not PushSubscription.objects.filter(token_id=self.token.pk).exists()

    def test_revoke_token_wrong_identity(self):
        other = User.register(email="other@test.com", username="otheruser")
        assert not Takahe.revoke_token(self.token.pk, other.identity.pk)
        assert Token.objects.filter(pk=self.token.pk).exists()

    def test_authorized_app_revoke_view(self):
        PushNotification.objects.create(
            token=self.token, type="mention", icon="", title="t", body="b"
        )
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        response = client.post(
            reverse("users:authorized_app_revoke"), {"token_id": self.token.pk}
        )
        assert response.status_code == 302
        assert not Token.objects.filter(pk=self.token.pk).exists()
