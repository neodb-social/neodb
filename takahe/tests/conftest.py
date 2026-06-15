import time

import pytest
from api.models import Application, Token
from core.models import Config
from django.test import Client
from stator.runner import StatorModel, StatorRunner

from users.models import Domain, Identity, User


@pytest.fixture
def keypair():
    """
    Testing-only keypair
    """
    return {
        "private_key": (
            "-----BEGIN PRIVATE"  # skip detect-private-key pre-commit
            " KEY-----\n"
            """MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCzNJa9JIxQpOtQ
z8UQKXDPREF9DyBliGu3uPWo6DMnkOm7hoh2+nOryrWDqWOFaVK//n7kltHXUEbm
U3exh0/0iWfzx2AbNrI04csAvW/hRvHbHBnVTotSxzqTd3ESkpcSW4xVuz9aCcFR
kW3unSCO3fF0Lh8Jsy9N/CT6oTnwG+ZpeGvHVbh9xfR5Ww6zA7z8A6B17hbzdMd/
3qUPijyIb5se4cWVtGg/ZJ0X1syn9u9kpwUjhHlyWH/esMRHxPuW49BPZPhhKs1+
t//4xgZcRX515qFqPS2EtYgZAfh7M3TRv8uCSzL4TT+8ka9IUwKdV6TFaqH27bAG
KyJQfGaTAgMBAAECggEALZY5qFjlRtiFMfQApdlc5KTw4d7Yt2tqN3zaJUMYTD7d
boJNMbMJfNCetyT+d6Aw2D1ly0GglNzLhGkEQElzKfpQUt/Lj3CtCa3Mpd4K2Wxi
NwJhgfUulPqwaHYQchCPVLCsNNziw0VLA7Rymionb6B+/TaEV8PYy0ZSo90ir3UD
CL5t+IWgIPiy6pk1wGOmeB+tU4+V7/hFel+vPFNahafqVhLE311dfx2aOfweAEfN
e4JoPeJP1/fB+BVZMyVSAraKz6wheymBBNKKn/vpFsdd6it2AP4UZeFp6ma9wT9t
nk65IpHg1MBxazQd7621GrPH+ZnhMg62H/FEj6rIDQKBgQC1w1fEbk+zjI54DXU8
FAe5cJbZS89fMP5CtzlWKzTzfdaavT+5cUYp3XAv37tSGsqYAXxY+4bHGa+qdCQO
I41cmylWGNX2e29/p2BspDPM6YQ0Z21MxFRBTWvHFrhd0bF1cXKBKPttdkKvzOEP
6uNy+/QtRNn9xF/ZjaMHcyPPTQKBgQD8ZdOmZ3TMsYJchAjjseN8S+Objw2oZzmK
6I1ULJBz3DWiyCUfir+pMjSH4fsAf9zrHkiM7xUgMByTukVRt16BrT7TlEBanAxc
/AKdNB3f0pza829LCz1lMAUn+ngZLTmRR+1rQFXqTjhB+0peJzKiMli+9BBhL9Ry
jMeTuLHdXwKBgGiz9kL5KIBNX2RYnEfXYfu4l6zktrgnCNB1q1mv2fjJbG4GxkaU
sc47+Pwa7VUGid22PWMkwSa/7SlLbdmXMT8/QjiOZfJueHQYfrsWe6B2g+mMCrJG
BiL37jXpKJsiyA7XIxaz/OG5VgDfDGaW8B60dJv/JXPBQ1WW+Wq5MM+hAoGAAUdS
xykHAnJzwpw4n06rZFnOEV+sJgo/1GBRNvfy02NuMiDpbzt4tRa4BWgzqVD8gYRp
wa0EYmFcA7OR3lQbenSyOMgre0oHFgGA0eMNs7CRctqA2dR4vyZ7IDS4nwgHnqDK
pxxwUvuKdWsceVWhgAjZQj5iRtvDK8Fi0XDCFekCgYALTU1v5iMIpaRAe+eyA2B1
42qm4B/uhXznvOu2YXU6iJFmMgHGYgpa+Dq8uUjKtpn/LIFeX1KN0hH8z/0LW3gB
e7tN7taW0oLK3RQcEMfkZ7diE9x3LGqo/xMxsZMtxAr88p5eMEU/nxxznOqq+W9b
qxRbXYzEtHz+cW9+FZkyVw==
-----END PRIVATE KEY-----"""
        ),
        "public_key": """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAszSWvSSMUKTrUM/FEClw
z0RBfQ8gZYhrt7j1qOgzJ5Dpu4aIdvpzq8q1g6ljhWlSv/5+5JbR11BG5lN3sYdP
9Iln88dgGzayNOHLAL1v4Ubx2xwZ1U6LUsc6k3dxEpKXEluMVbs/WgnBUZFt7p0g
jt3xdC4fCbMvTfwk+qE58BvmaXhrx1W4fcX0eVsOswO8/AOgde4W83THf96lD4o8
iG+bHuHFlbRoP2SdF9bMp/bvZKcFI4R5clh/3rDER8T7luPQT2T4YSrNfrf/+MYG
XEV+deahaj0thLWIGQH4ezN00b/Lgksy+E0/vJGvSFMCnVekxWqh9u2wBisiUHxm
kwIDAQAB
-----END PUBLIC KEY-----""",
        "public_key_id": "https://example.com/test-actor#test-key",
    }


@pytest.fixture(autouse=True)
def _bypass_ssrf_check(monkeypatch):
    """Disable SSRF DNS check in tests so pytest_httpx mocks work with fake domains."""
    _noop = lambda request: None  # noqa: E731
    monkeypatch.setattr("core.files.check_url_safety", _noop)
    monkeypatch.setattr("core.signatures.check_url_safety", _noop)
    monkeypatch.setattr("users.models.identity.check_url_safety", _noop)


@pytest.fixture(autouse=True)
def _test_settings(settings):
    # We use `StaticFilesStorage` instead of `ManifestStaticFilesStorage` in tests
    # since want stable filenames (`css/styles.css`) instead of hashed (`css/styles.55e7cbb9ba48.css`)
    settings.STORAGES = {
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
    }
    settings.SETUP.MAIN_DOMAIN = "example.com"
    settings.MAIN_DOMAIN = "example.com"


@pytest.fixture
def config_system(keypair):
    Config.system = Config.SystemOptions(
        system_actor_private_key=keypair["private_key"],
        system_actor_public_key=keypair["public_key"],
    )
    Config.__forced__ = True
    yield Config.system
    Config.__forced__ = False
    del Config.system


@pytest.fixture
def client_with_user(client, user):
    """
    Provides a logged-in test client
    """
    client.force_login(user)
    return client


@pytest.fixture
def user(db) -> User:
    return User.objects.create(email="test@example.com")


@pytest.fixture
def domain(db) -> Domain:
    return Domain.objects.create(
        domain="example.com", local=True, public=True, state="updated"
    )


@pytest.fixture
def domain2(db) -> Domain:
    return Domain.objects.create(
        domain="example2.com", local=True, public=True, state="updated"
    )


@pytest.fixture
def identity_factory(user, domain, keypair):
    """
    Factory for creating identities with custom parameters
    """

    def _create_identity(username="test", actor_type="person", **kwargs):
        # Only set default name if not provided in kwargs
        if "name" not in kwargs:
            kwargs["name"] = f"{username.title()} User"

        identity = Identity.objects.create(
            actor_uri=f"https://example.com/@{username}@example.com/",
            inbox_uri=f"https://example.com/@{username}@example.com/inbox/",
            private_key=keypair["private_key"],
            public_key=keypair["public_key"],
            username=username,
            domain=domain,
            actor_type=actor_type,
            local=True,
            **kwargs,
        )
        identity.users.set([user])
        return identity

    return _create_identity


@pytest.fixture
def identity(identity_factory) -> Identity:
    """
    Creates a basic test identity with a user and domain.
    """
    return identity_factory(username="test", actor_type="person", name="Test User")


@pytest.fixture
def identity2(user, domain2) -> Identity:
    """
    Creates a basic test identity with a user and domain.
    """
    identity = Identity.objects.create(
        actor_uri="https://example2.com/@test@example2.com/",
        username="test",
        domain=domain2,
        name="Test User Domain2",
        local=True,
    )
    identity.users.set([user])
    return identity


@pytest.fixture
def other_identity(user, domain) -> Identity:
    """
    Creates a different basic test identity with a user and domain.
    """
    identity = Identity.objects.create(
        actor_uri="https://example.com/@other@example.com/",
        inbox_uri="https://example.com/@other@example.com/inbox/",
        username="other",
        domain=domain,
        name="Other User",
        local=True,
    )
    identity.users.set([user])
    return identity


@pytest.fixture
def remote_identity(db) -> Identity:
    """
    Creates a basic remote test identity with a domain.
    """
    domain = Domain.objects.create(domain="remote.test", local=False, state="updated")
    return Identity.objects.create(
        actor_uri="https://remote.test/test-actor/",
        inbox_uri="https://remote.test/@test/inbox/",
        profile_uri="https://remote.test/@test/",
        featured_collection_uri="https://remote.test/test-actor/collections/featured/",
        featured_tags_uri="https://remote.test/test-actor/collections/tags/",
        username="test",
        domain=domain,
        name="Test Remote User",
        local=False,
        state="updated",
    )


@pytest.fixture
def remote_identity2(db) -> Identity:
    """
    Creates a basic remote test identity with a domain.
    """
    domain = Domain.objects.create(domain="remote2.test", local=False)
    return Identity.objects.create(
        actor_uri="https://remote2.test/test-actor/",
        profile_uri="https://remote2.test/@test/",
        username="test",
        domain=domain,
        name="Test2 Remote User",
        local=False,
    )


@pytest.fixture
def api_token(identity) -> Token:
    """
    Creates an API application, an identity, and a token for that identity
    """
    application = Application.objects.create(
        name="Test App",
        client_id="tk-test",
        client_secret="mytestappsecret",
    )
    return Token.objects.create(
        application=application,
        user=identity.users.first(),
        identity=identity,
        token="mytestapitoken",
        scopes=["read", "write", "follow", "push"],
    )


@pytest.fixture
def api_client(api_token):
    return Client(
        headers={
            "authorization": f"Bearer {api_token.token}",
            "accept": "application/json",
        }
    )


@pytest.fixture
def stator(config_system) -> StatorRunner:
    """
    Return an initialized StatorRunner for tests that need state transitioning
    to happen.
    """
    runner = StatorRunner(
        StatorModel.subclasses,
        concurrency=100,
        schedule_interval=30,
    )
    runner.handled = {}
    runner.started = time.monotonic()
    runner.last_clean = time.monotonic() - runner.schedule_interval
    runner.tasks = []

    return runner
