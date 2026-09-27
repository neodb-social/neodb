import pytest
from pytest_httpx import HTTPXMock

from users.models import Domain
from users.models.domain import DomainStates


def test_valid_domain():
    """
    Tests that a valid domain is valid
    """

    assert Domain.is_valid_domain("example.com")
    assert Domain.is_valid_domain("xn----gtbspbbmkef.xn--p1ai")
    assert Domain.is_valid_domain("underscore_subdomain.example.com")
    assert Domain.is_valid_domain("something.versicherung")
    assert Domain.is_valid_domain("11.com")
    assert Domain.is_valid_domain("a.cn")
    assert Domain.is_valid_domain("sub1.sub2.sample.co.uk")
    assert Domain.is_valid_domain("somerandomexample.xn--fiqs8s")
    assert not Domain.is_valid_domain("über.com")
    assert not Domain.is_valid_domain("example.com:4444")
    assert not Domain.is_valid_domain("example.-com")
    assert not Domain.is_valid_domain("foo@bar.com")
    assert not Domain.is_valid_domain("example.")
    assert not Domain.is_valid_domain("example.com.")
    assert not Domain.is_valid_domain("-example.com")
    assert not Domain.is_valid_domain("_example.com")
    assert not Domain.is_valid_domain("_example._com")
    assert not Domain.is_valid_domain("example_.com")
    assert not Domain.is_valid_domain("example")
    assert not Domain.is_valid_domain("a......b.com")
    assert not Domain.is_valid_domain("a.123")
    assert not Domain.is_valid_domain("123.123")
    assert not Domain.is_valid_domain("123.123.123.123")


@pytest.mark.django_db
def test_recursive_block():
    """
    Tests that blocking a domain also blocks its subdomains
    """

    root_domain = Domain.get_remote_domain("evil.com")
    root_domain.blocked = True
    root_domain.save()

    # Re-fetching the root should be blocked
    assert Domain.get_remote_domain("evil.com").recursively_blocked()

    # A sub domain should also be blocked
    assert Domain.get_remote_domain("terfs.evil.com").recursively_blocked()

    # An unrelated domain should not be blocked
    assert not Domain.get_remote_domain("example.com").recursively_blocked()


def test_fetch_nodeinfo_invalid_idna_host():
    """
    An unencodable host raises from httpx.URL.host while the request is built,
    outside the httpx.HTTPError tree, so it used to escape to Stator.
    """

    assert Domain(domain="xn--4t8h.example").fetch_nodeinfo() is None


@pytest.mark.django_db
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_nodeinfo_with_nul_and_nan_is_saved(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://nul.example/.well-known/nodeinfo",
        status_code=404,
    )
    httpx_mock.add_response(
        url="https://nul.example/nodeinfo/2.0",
        headers={"Content-Type": "application/json"},
        content=(
            b'{"version": "2.0", "software": {"name": "x\\u0000y", "version": "1"},'
            b' "openRegistrations": false, "usage": {"users": {}},'
            b' "metadata": {"nodeName": "a\\u0000b", "ratio": NaN}}'
        ),
    )
    domain = Domain.objects.create(domain="nul.example", local=False)
    assert DomainStates.handle_outdated(domain) == DomainStates.updated
    domain.refresh_from_db()
    assert domain.nodeinfo["software"]["name"] == "xy"
    assert domain.nodeinfo["metadata"] == {"nodeName": "ab", "ratio": None}
