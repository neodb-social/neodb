#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["atproto>=0.0.55", "dnspython>=2.7.0"]
# ///
"""Publish net.neodb.* lexicon schemas for ATProto lexicon resolution.

Standalone script (no Django): run it with ``uv run docs/lexicons/publish.py``
or any Python with ``atproto`` and ``dnspython`` installed.

Each JSON file in this directory tree is written to the project ATProto
account's repo as a ``com.atproto.lexicon.schema`` record keyed by its NSID.
Together with a ``_lexicon.<authority domain>`` DNS TXT record pointing at the
account's DID, this makes the schemas resolvable by tools and appviews.
Re-running overwrites the records in place, so publishing an updated lexicon
is the same command again.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import dns.resolver
from atproto import Client
from atproto_client import models
from atproto_identity.did.resolver import DidResolver
from atproto_identity.handle.resolver import HandleResolver

LEXICON_DIR = Path(__file__).resolve().parent
LEXICON_COLLECTION = "com.atproto.lexicon.schema"


def load_lexicons(lexicon_dir: Path = LEXICON_DIR) -> list[tuple[str, dict[str, Any]]]:
    docs = []
    for path in sorted(lexicon_dir.glob("**/*.json")):
        doc = json.loads(path.read_text())
        nsid = doc.get("id")
        if doc.get("lexicon") != 1 or not nsid:
            raise ValueError(f"{path}: not a valid lexicon document")
        docs.append((nsid, doc))
    return docs


def authority_domain(nsid: str) -> str:
    """Authority domain of an NSID, e.g. net.neodb.mark -> neodb.net."""
    return ".".join(reversed(nsid.split(".")[:-1]))


def check_dns(domain: str, did: str) -> bool:
    name = f"_lexicon.{domain}"
    expected = f"did={did}"
    try:
        answers = dns.resolver.resolve(name, "TXT")
        values = [b"".join(r.strings).decode() for r in answers]  # type: ignore[attr-defined]
    except Exception:
        values = []
    if expected in values:
        print(f'DNS OK: {name} TXT "{expected}"')
        return True
    print(f'WARNING: DNS record missing: add TXT at {name} with value "{expected}"')
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--handle",
        default="neodb.net",
        help="handle of the ATProto account hosting the schemas",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="app password; defaults to ATPROTO_APP_PASSWORD env var",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list lexicon documents without publishing",
    )
    args = parser.parse_args(argv)

    docs = load_lexicons()
    if not docs:
        print(f"no lexicon files found in {LEXICON_DIR}", file=sys.stderr)
        return 1
    for nsid, _ in docs:
        print(f"found {nsid}")
    if args.dry_run:
        return 0

    password = args.password or os.environ.get("ATPROTO_APP_PASSWORD")
    if not password:
        print("provide --password or set ATPROTO_APP_PASSWORD", file=sys.stderr)
        return 1
    did = HandleResolver(timeout=5).resolve(args.handle)
    if not did:
        print(f"cannot resolve handle {args.handle}", file=sys.stderr)
        return 1
    did_doc = DidResolver().resolve(did)
    if not did_doc:
        print(f"cannot resolve did {did}", file=sys.stderr)
        return 1
    client = Client(did_doc.get_pds_endpoint())
    client.login(args.handle, password)
    for nsid, doc in docs:
        r = client.com.atproto.repo.put_record(
            models.ComAtprotoRepoPutRecord.Data(
                repo=did,
                collection=LEXICON_COLLECTION,
                rkey=nsid,
                record={**doc, "$type": LEXICON_COLLECTION},
            )
        )
        print(f"published {r.uri}")
    for domain in sorted({authority_domain(nsid) for nsid, _ in docs}):
        check_dns(domain, did)
    return 0


if __name__ == "__main__":
    sys.exit(main())
