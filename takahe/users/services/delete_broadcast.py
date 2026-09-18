import json
import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.db import connection
from django.utils.http import http_date

from core.files import make_safe_client
from core.ld import canonicalise
from core.signatures import HttpSignature

if TYPE_CHECKING:
    from users.models import Identity

logger = logging.getLogger(__name__)

# Namespace for pg_try_advisory_lock, so the key cannot collide with another
# feature's advisory lock. The second key is derived from the identity id.
ADVISORY_LOCK_NAMESPACE = 0x7AC4
ADVISORY_LOCK_MODULUS = 2**31

# Stator hands a handler a row locked for 300 s, and clears the lock after
# that while the thread keeps running, which lets a second replica re-enter
# the handler. The batch stops dispatching well before then so the whole
# handler, including the in-flight tail and the transition write, finishes
# inside the window.
DEFAULT_DEADLINE = 200.0
DEFAULT_CONCURRENCY = 200
# Connect is the term that matters: a black-holed host burns all of it.
DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)


@contextmanager
def identity_broadcast_lock(identity_pk: int) -> Iterator[bool]:
    """
    Session advisory lock naming one identity's delete broadcast.

    Yields whether it was acquired. Stator runs handlers in threads and Django
    keeps one connection per thread, so the lock belongs to this handler's own
    connection and is released by the finally, which matters because a
    persistent connection would otherwise hold it for the process lifetime.
    """
    key = identity_pk % ADVISORY_LOCK_MODULUS
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", [ADVISORY_LOCK_NAMESPACE, key]
        )
        acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s, %s)",
                        [ADVISORY_LOCK_NAMESPACE, key],
                    )
            except Exception:
                logger.exception("Could not release broadcast lock for %s", identity_pk)


class DeleteBroadcaster:
    """
    Sends one Delete(actor) to many inboxes, best effort.

    Nothing is persisted and nothing is retried: the peers that matter get a
    durable FanOut instead. This exists so the remaining peers, which for a
    large server is most of them, cost no queue rows at all.
    """

    def __init__(
        self,
        identity: "Identity",
        deadline: float = DEFAULT_DEADLINE,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ):
        self.identity = identity
        self.deadline = deadline
        self.concurrency = concurrency
        self.timeout = timeout
        self.transport = transport
        # The body is the same for every peer, so it is built once. Only the
        # signed headers differ, because they cover the target and the host.
        self.body = json.dumps(canonicalise(identity.to_delete_ap())).encode("utf8")
        self.digest = HttpSignature.calculate_digest(self.body)
        self.key_id = identity.public_key_id
        # Parsing the PEM is the expensive part of signing, and signed_request
        # repeats it for every single delivery.
        self.private_key = cast(
            rsa.RSAPrivateKey,
            serialization.load_pem_private_key(
                (identity.private_key or "").encode("ascii"), password=None
            ),
        )

    def headers_for(self, uri: str) -> dict[str, str]:
        uri_parts = urlparse(uri)
        headers = {
            "(request-target)": f"post {uri_parts.path}",
            "Host": uri_parts.hostname or "",
            "Date": http_date(),
            "Digest": self.digest,
            "Content-Type": "application/activity+json",
        }
        signed_string = "\n".join(
            f"{name.lower()}: {value}" for name, value in headers.items()
        )
        signature = self.private_key.sign(
            signed_string.encode("utf8"), padding.PKCS1v15(), hashes.SHA256()
        )
        headers["Signature"] = HttpSignature.compile_signature(
            {
                "keyid": self.key_id,
                "headers": list(headers.keys()),
                "signature": signature,
                "algorithm": "rsa-sha256",
            }
        )
        headers["User-Agent"] = settings.TAKAHE_USER_AGENT
        del headers["(request-target)"]
        return headers

    def deliver(self, client: httpx.Client, uri: str) -> bool:
        try:
            client.post(uri, headers=self.headers_for(uri), content=self.body)
        except Exception as error:
            # Best effort: a peer that cannot be reached is simply skipped.
            # It still learns of the deletion when it next pulls the actor.
            logger.debug("Delete broadcast to %s failed: %s", uri, error)
            return False
        return True

    def send(self, uris: list[str]) -> tuple[int, int]:
        """
        Returns how many were attempted and how many were accepted.
        """
        if settings.SETUP.NO_FEDERATION or not uris:
            return 0, 0
        started = time.monotonic()
        attempted = 0
        futures = []
        limits = httpx.Limits(max_connections=self.concurrency)
        with make_safe_client(
            timeout=self.timeout,
            limits=limits,
            transport=self.transport,
            # A redirected POST would arrive with a signature that no longer
            # covers its target, exactly as signed_request avoids for deliveries.
            follow_redirects=False,
        ) as client:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                for uri in uris:
                    if time.monotonic() - started >= self.deadline:
                        logger.warning(
                            "Delete broadcast for %s hit its %ss deadline after %s of %s inboxes",
                            self.identity.pk,
                            self.deadline,
                            attempted,
                            len(uris),
                        )
                        break
                    futures.append(pool.submit(self.deliver, client, uri))
                    attempted += 1
        delivered = sum(1 for future in futures if future.result())
        logger.info(
            "Delete broadcast for %s: %s of %s attempted inboxes accepted it",
            self.identity.pk,
            delivered,
            attempted,
        )
        return attempted, delivered


def broadcast_identity_deletion(
    identity: "Identity", uris: list[str]
) -> tuple[int, int]:
    """
    Best-effort Delete(actor) to inboxes that get no FanOut of their own.
    """
    return DeleteBroadcaster(identity).send(uris)
