# VinylHub M0 NeoDB admission record

This record establishes the exact upstream-traceable NeoDB Product-backend baseline for owner Issue [mirrorforce/neodb#1](https://github.com/mirrorforce/neodb/issues/1), under app contract [mirrorforce/vinyl-catalog-app#66](https://github.com/mirrorforce/vinyl-catalog-app/issues/66) and program Issue [#88](https://github.com/mirrorforce/vinyl-catalog-app/issues/88).

## Admission identity

Recorded on 2026-09-01 from a clean checkout before repository-local changes:

| Identity | Value |
| --- | --- |
| Fork | `mirrorforce/neodb` |
| Upstream | `neodb-social/neodb` |
| Default branch | `main` |
| Upstream candidate | `upstream/main` |
| Upstream commit | `3e5bf691c2be4dc786a19f5603a4239b6935d6f7` |
| Fork source commit | `3e5bf691c2be4dc786a19f5603a4239b6935d6f7` |
| Upstream tree | `9e121f6d24073a38de3deda0e3fb258e6e6b000a` |
| Fork tree | `9e121f6d24073a38de3deda0e3fb258e6e6b000a` |
| Commit/tree divergence | none; 1,627 tracked paths on each tree |

The admitted source baseline is exactly that upstream commit and tree. The owner PR adds only the repository execution overlay and this admission record; the upstream Product backend remains unchanged.

## Native conventions qualified

| Seam | Native evidence | M0 result |
| --- | --- | --- |
| User/session/account | `neodb/users/`, Django settings and tests | Present; no identity behavior changed |
| APIdentity/Takahē | `neodb/users/models/apidentity.py`, `neodb/takahe/`, two database settings | Present; the NeoDB/Takahē split is preserved |
| Journal Product surfaces | `neodb/journal/` models, APIs, migrations, and tests | Review/Shelf/Mark/Collection present; no feature delta |
| Social accounts | `neodb/mastodon/models/common.py`, `mastodon.py`, tests | SocialAccount/MastodonAccount present; no integration delta |
| Persistence and jobs | PostgreSQL settings/migrations, RQ/Redis settings, Typesense settings | Existing upstream wiring preserved |
| Runtime | `compose.yml`, `Dockerfile`, `misc/bin/neodb-init`, CI workflows | Docker/management/test conventions preserved |

The existing upstream `CollectionItem` API/model references are baseline context only. M0 adds no CollectionItem, ManagedIdentityBinding/OneID, Pixelfed, Product schema/API, generic framework/provider graph, upstream-history, or production-cutover work.

## Tracking and delivery policy

- `upstream/main` is periodically fresh-read for tracking and drift qualification. A moving upstream branch is never silently promoted to a VinylHub release.
- An admitted baseline is an exact upstream commit plus tree identity, recorded in the owner PR and accepted by Controller through the app #66 gate.
- VinylHub delivery identity is downstream and reviewable: task branch -> owner PR -> reviewed merge commit. A future release must additionally name its immutable tag and/or image digest; it must not reuse an upstream tag or rewrite upstream history.
- The default `main` branch remains the repository's upstream-aligned line. M0 work is delivered from a task branch and does not normalize upstream branch names.

## Validation record

The following checks are required for this admission and are run from the task branch after the overlay is authored:

```text
git status --porcelain=v2 --untracked-files=all
git diff --check
git diff upstream/main...HEAD --name-status
uv sync --frozen
uv run --project . python manage.py check       (working directory: neodb)
uv run --project . python manage.py makemigrations --check --dry-run (working directory: neodb)
uv run --project . python -m pytest --collect-only -q (working directory: neodb)
uvx --with pre_commit pre-commit run -a --show-diff-on-failure
docker compose config --quiet
```

Observed on the M0 task branch:

- `uv sync --frozen`: **PASS** with Python 3.14.3 and the locked dependency set.
- Linux Docker build from the tree: **PASS**, including Django static compilation and collection.
- Containerized `manage.py check`: **PASS** (`2 silenced`).
- PostgreSQL migrations for both the Takahē and NeoDB databases: **PASS** using the equivalent direct Django entrypoints after the Windows checkout's CRLF wrapper limitation was isolated. Post-migration setup completed.
- Bounded Linux dev-image smoke (`tests/core/test_validators.py` and `tests/users/test_models.py`): **PASS**, 75 passed and 14 upstream dependency warnings, with Typesense 30.1 aligned to the CI workflow.
- `docker compose config --quiet`: **PASS**. The configuration emits only expected missing-local-`.env` warnings.
- `makemigrations --check --dry-run`: **NOT A CLEAN BASELINE CHECK**. The unchanged upstream tree proposes `catalog` migration `0028_alter_externalresource_id_type_and_more.py` for six field alterations. No migration was added in M0; this is recorded as upstream baseline drift for Controller review.
- Typesense 30.2, the current Compose default, exited 139 without logs in this Docker host. The CI-aligned 30.1 service supported the bounded smoke; no runtime version change is included in the repository overlay.

The Windows host cannot import the upstream `fcntl` dependency directly, and its CRLF checkout prevents direct execution of copied shell wrappers inside Linux containers. These are environment/path observations only; no upstream source file was normalized or changed. Any unavailable service or host-native check is recorded as `NOT RUN` or baseline/tooling evidence rather than represented as a pass.

## M0 non-changes

No Product feature source, schema, migration, API, account orchestration, OneID binding, Pixelfed integration, generic framework, provider graph, upstream history, production configuration, secrets, credentials, or private user data is included in this admission.
