import pytest
from django.conf import settings

from api.models import PushNotification, PushSubscription
from api.models.push import (
    PUSH_TIMEOUT,
    PushNotificationStates,
    PushType,
    is_valid_push_endpoint,
)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://1.1.1.1/push",
        "https://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1:9200/_search",
        "https://10.0.0.1/push",
        "ftp://1.1.1.1/push",
        "not a url",
    ],
)
def test_push_endpoint_refused(endpoint):
    assert not is_valid_push_endpoint(endpoint)


def test_push_endpoint_public_https_accepted():
    assert is_valid_push_endpoint("https://1.1.1.1/push/abc")


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_webpush(subscription_info, data, **kwargs) -> None:
        calls.append({"endpoint": subscription_info["endpoint"], **kwargs})

    monkeypatch.setattr("api.models.push.webpush", fake_webpush)
    monkeypatch.setattr(settings.SETUP, "VAPID_PRIVATE_KEY", "test-key")
    monkeypatch.setattr(settings.SETUP, "VAPID_PUBLIC_KEY", "test-public-key")
    return calls


def _notification(api_token, endpoint: str) -> PushNotification:
    PushSubscription.objects.create(
        token=api_token, endpoint=endpoint, keys={}, alerts={}
    )
    return PushNotification.objects.create(
        token=api_token, type=PushType.mention, icon="", title="t", body=""
    )


@pytest.mark.django_db
def test_push_to_internal_endpoint_not_sent(api_token, sent):
    notification = _notification(api_token, "https://169.254.169.254/push")
    assert (
        PushNotificationStates.handle_sending(notification)
        == PushNotificationStates.failed
    )
    assert sent == []


@pytest.mark.django_db
def test_push_sent_with_timeout_and_tls_verification(api_token, sent):
    notification = _notification(api_token, "https://1.1.1.1/push")
    assert (
        PushNotificationStates.handle_sending(notification)
        == PushNotificationStates.sent
    )
    assert len(sent) == 1
    assert sent[0]["timeout"] == PUSH_TIMEOUT
    session = sent[0]["requests_session"]
    assert session.verify is True
    assert session.max_redirects == 0


def _subscribe(api_client, endpoint: str):
    return api_client.post(
        "/api/v1/push/subscription",
        content_type="application/json",
        data={
            "subscription": {
                "endpoint": endpoint,
                "keys": {"p256dh": "key", "auth": "secret"},
            },
            "data": {"alerts": {"mention": True}},
        },
    )


@pytest.mark.django_db
def test_subscription_to_internal_endpoint_refused(api_client, api_token, sent):
    response = _subscribe(api_client, "https://169.254.169.254/latest/meta-data/")
    assert response.status_code == 422
    assert not PushSubscription.objects.filter(token=api_token).exists()


@pytest.mark.django_db
def test_subscription_to_public_endpoint_accepted(api_client, api_token, sent):
    response = _subscribe(api_client, "https://1.1.1.1/push/abc")
    assert response.status_code == 200
    assert PushSubscription.objects.get(token=api_token).endpoint == (
        "https://1.1.1.1/push/abc"
    )
