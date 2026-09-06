import httpx
import pytest
from pytest_httpx import HTTPXMock

from activities.models import FanOut, FanOutStates, Post, PostInteraction
from stator.exceptions import TryAgainLater
from users.models import Identity

INBOX = "https://remote.test/@test/inbox/"


@pytest.fixture(autouse=True)
def _enable_federation(settings):
    original = settings.SETUP.NO_FEDERATION
    settings.SETUP.NO_FEDERATION = False
    yield
    settings.SETUP.NO_FEDERATION = original


def _post_fan_out(identity: Identity, remote_identity: Identity) -> FanOut:
    post = Post.create_local(author=identity, content="<p>Hello</p>")
    return FanOut.objects.create(
        identity=remote_identity,
        type=FanOut.Types.post,
        subject_post=Post.objects.get(pk=post.pk),
    )


def _interaction_fan_out(identity: Identity, remote_identity: Identity) -> FanOut:
    post = Post.create_local(author=identity, content="<p>Hello</p>")
    interaction = PostInteraction.objects.create(
        identity=identity,
        post=Post.objects.get(pk=post.pk),
        type=PostInteraction.Types.like,
    )
    return FanOut.objects.create(
        identity=remote_identity,
        type=FanOut.Types.interaction,
        subject_post=interaction.post,
        subject_post_interaction=interaction,
    )


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_refused_interaction_delivery_is_not_resent(
    identity: Identity,
    remote_identity: Identity,
    config_system,
    stator,
    httpx_mock: HTTPXMock,
):
    """
    An inbox that refuses with a 403 refuses every resend, so the fan-out
    fails instead of retrying for days.
    """
    fan_out = _interaction_fan_out(identity, remote_identity)
    httpx_mock.add_response(
        url=INBOX,
        status_code=403,
        content=b"Forbidden",
    )

    stator.run_single_cycle()
    assert FanOut.objects.get(pk=fan_out.pk).state == FanOutStates.failed
    # And nothing sends it a second time
    stator.run_single_cycle()
    assert len(httpx_mock.get_requests(url=INBOX, method="POST")) == 1


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_refused_post_delivery_fails(
    identity: Identity,
    remote_identity: Identity,
    config_system,
    httpx_mock: HTTPXMock,
):
    """
    Refusals are handled the same way for every remote delivery type.
    """
    fan_out = _post_fan_out(identity, remote_identity)
    httpx_mock.add_response(url=INBOX, status_code=400, content=b"nope")

    assert FanOutStates.handle_new(fan_out) == FanOutStates.failed


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_deferred_delivery_is_retried(
    identity: Identity,
    remote_identity: Identity,
    config_system,
    stator,
    httpx_mock: HTTPXMock,
):
    """
    A retryable refusal keeps the fan-out queued.
    """
    fan_out = _post_fan_out(identity, remote_identity)
    httpx_mock.add_response(url=INBOX, status_code=429)

    with pytest.raises(TryAgainLater):
        FanOutStates.handle_new(FanOut.objects.get(pk=fan_out.pk))
    stator.run_single_cycle()
    assert FanOut.objects.get(pk=fan_out.pk).state == FanOutStates.new


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_unreachable_inbox_is_retried(
    identity: Identity,
    remote_identity: Identity,
    config_system,
    stator,
    httpx_mock: HTTPXMock,
):
    """
    A connection failure is not a refusal; the fan-out stays queued.
    """
    fan_out = _post_fan_out(identity, remote_identity)
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))

    stator.run_single_cycle()
    assert FanOut.objects.get(pk=fan_out.pk).state == FanOutStates.new


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_accepted_delivery_is_sent(
    identity: Identity,
    remote_identity: Identity,
    config_system,
    httpx_mock: HTTPXMock,
):
    fan_out = _post_fan_out(identity, remote_identity)
    httpx_mock.add_response(url=INBOX, status_code=202)

    assert FanOutStates.handle_new(fan_out) == FanOutStates.sent
