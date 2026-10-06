from unittest import mock

import httpx
import pytest

from takahe.models import Domain, Identity

EVIL_ACTOR = "https://evil.test/users/eve"
SPLIT_ACTOR = "https://social.split.test/users/alice"


def _webfinger(answers: dict[str, tuple[str | None, str | None]]):
    return mock.patch.object(
        Identity,
        "fetch_webfinger",
        side_effect=lambda handle: answers.get(handle, (None, None)),
    )


@pytest.mark.django_db(databases="__all__")
def test_fetch_refuses_subject_on_unconfirming_domain() -> None:
    with _webfinger(
        {
            "eve@evil.test": (EVIL_ACTOR, "someone@victim.test"),
            "someone@victim.test": (
                "https://victim.test/users/someone",
                "someone@victim.test",
            ),
        }
    ):
        found = Identity.by_username_and_domain("eve", "evil.test", fetch=True)
    assert found is not None
    identity = Identity.objects.get(pk=found.pk)
    assert identity.actor_uri == EVIL_ACTOR
    assert (identity.username, identity.domain_id) == ("eve", "evil.test")


@pytest.mark.django_db(databases="__all__")
def test_fetch_adopts_subject_confirmed_by_its_domain() -> None:
    with _webfinger(
        {
            "alice@social.split.test": (SPLIT_ACTOR, "alice@split.test"),
            "alice@split.test": (SPLIT_ACTOR, "alice@split.test"),
        }
    ):
        found = Identity.by_username_and_domain(
            "alice", "social.split.test", fetch=True
        )
    assert found is not None
    identity = Identity.objects.get(pk=found.pk)
    assert (identity.username, identity.domain_id) == ("alice", "split.test")


@pytest.mark.django_db(databases="__all__")
def test_subject_on_local_domain_is_refused_without_lookup() -> None:
    local = Domain.objects.filter(local=True).first() or Domain.objects.create(
        domain="local.test", local=True
    )
    claimed = f"admin@{local.domain}"
    with _webfinger({"eve@evil.test": (EVIL_ACTOR, claimed)}) as lookup:
        assert (
            Identity.confirm_webfinger_handle(EVIL_ACTOR, "eve@evil.test", claimed)
            is False
        )
    lookup.assert_not_called()


@pytest.mark.django_db(databases="__all__")
def test_webfinger_redirect_to_internal_host_is_refused() -> None:
    """Every redirect hop is checked, not only the URL first requested."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host)
        if request.url.path.endswith("host-meta"):
            return httpx.Response(404)
        if request.url.host == "evil.test":
            return httpx.Response(302, headers={"Location": "https://internal.test/"})
        return httpx.Response(
            200,
            json={
                "subject": "acct:eve@evil.test",
                "links": [
                    {
                        "rel": "self",
                        "type": "application/activity+json",
                        "href": EVIL_ACTOR,
                    }
                ],
            },
        )

    real_client = httpx.Client
    with (
        mock.patch(
            "takahe.models.is_valid_url", side_effect=lambda url: "internal" not in url
        ),
        mock.patch(
            "takahe.models.httpx.Client",
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        ),
    ):
        assert Identity.fetch_webfinger("eve@evil.test") == (None, None)
    assert "internal.test" not in requested
