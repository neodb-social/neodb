import base64
import time
from email.utils import formatdate

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.test import RequestFactory

from takahe.auth import verify_http_signature
from takahe.models import Domain, Identity


def keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


def signed_request(private_pem: str, key_id: str):
    path = "/collection/1/"
    host = "testserver"
    date = formatdate(timeval=time.time(), usegmt=True)
    cleartext = (f"(request-target): get {path}\nhost: {host}\ndate: {date}").encode(
        "utf-8"
    )
    private_key = serialization.load_pem_private_key(
        private_pem.encode("ascii"), password=None
    )
    assert isinstance(private_key, rsa.RSAPrivateKey)
    signature = base64.b64encode(
        private_key.sign(cleartext, padding.PKCS1v15(), hashes.SHA256())
    ).decode("ascii")
    return RequestFactory().get(
        path,
        headers={
            "host": host,
            "date": date,
            "signature": (
                f'keyId="{key_id}",algorithm="rsa-sha256",'
                f'headers="(request-target) host date",signature="{signature}"'
            ),
        },
    )


@pytest.mark.django_db(databases="__all__")
def test_signature_by_an_alias_key_resolves_to_the_canonical_identity():
    """
    A peer signs with the key of the actor under the URI it knows, which may
    be one a merge emptied. Answering as that row would lose the follows the
    canonical identity now holds, and an emptied row has no domain, so
    building a mirror for it raises instead.
    """
    private_pem, public_pem = keypair()
    domain = Domain.get_remote_domain("remote.example")
    canonical = Identity.objects.create(
        actor_uri="https://remote.example/ruben",
        username="ruben",
        domain=domain,
        local=False,
        public_key=public_pem,
        public_key_id="https://remote.example/ruben#main-key",
    )
    alias = Identity.objects.create(
        actor_uri="https://remote.example/users/ruben",
        local=False,
        canonical=canonical,
        public_key=public_pem,
        public_key_id="https://remote.example/users/ruben#main-key",
    )

    apidentity = verify_http_signature(signed_request(private_pem, alias.public_key_id))

    assert apidentity.pk == canonical.pk
    assert apidentity.username == "ruben"


@pytest.mark.django_db(databases="__all__")
def test_relationship_against_a_merged_id_lands_on_the_real_identity():
    """
    A mirror row kept because it owns data still carries the alias id, and a
    block written against the emptied row is one the actor never sees.
    """
    from takahe.models import Block
    from takahe.utils import Takahe

    domain = Domain.get_remote_domain("remote.example")
    local = Identity.objects.create(
        actor_uri="https://testserver/@me/",
        username="me",
        domain=Domain.objects.create(domain="testserver", local=True),
        local=True,
    )
    canonical = Identity.objects.create(
        actor_uri="https://remote.example/ruben",
        username="ruben",
        domain=domain,
        local=False,
    )
    alias = Identity.objects.create(
        actor_uri="https://remote.example/users/ruben",
        local=False,
        canonical=canonical,
    )

    Takahe.block_or_mute(local.pk, alias.pk, False)

    assert Block.objects.filter(source=local, target=canonical, mute=False).exists()
    assert not Block.objects.filter(target=alias).exists()


@pytest.mark.django_db(databases="__all__")
def test_relationship_reads_see_what_the_writes_did():
    """
    Resolving writes but not reads is worse than resolving neither: the block
    lands on the identity behind the alias while every check that guards
    content still asks about the alias and sees nothing.
    """
    from takahe.utils import Takahe

    domain = Domain.get_remote_domain("remote.example")
    local = Identity.objects.create(
        actor_uri="https://testserver/@me2/",
        username="me2",
        domain=Domain.objects.create(domain="testserver2", local=True),
        local=True,
    )
    canonical = Identity.objects.create(
        actor_uri="https://remote.example/ruben2",
        username="ruben2",
        domain=domain,
        local=False,
    )
    alias = Identity.objects.create(
        actor_uri="https://remote.example/users/ruben2",
        local=False,
        canonical=canonical,
    )

    Takahe.block_or_mute(local.pk, alias.pk, False)

    assert Takahe.get_is_blocking(local.pk, alias.pk)
    assert Takahe.get_is_blocking(local.pk, canonical.pk)

    Takahe.follow(local.pk, alias.pk, force_accept=True)
    assert Takahe.get_is_following(local.pk, alias.pk)
    assert Takahe.get_is_following(local.pk, canonical.pk)
