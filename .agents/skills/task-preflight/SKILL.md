---
name: task-preflight
description: Preflight NeoDB repository work against current authority, checkout state, scope, and one-writer safety before any non-trivial change.
---

# Task preflight

Use this skill before any non-trivial work in this repository.

1. Read the repository-root `AGENTS.md` if present, the current task handoff, the linked app contract, and the local owner Issue. Treat those sources as scope and authority; do not infer Product requirements from source code.
2. Fresh-read the current default branch, `HEAD`, fork remote, and upstream remote. Record the exact commit identities before comparing or changing anything.
3. Confirm the checkout is clean, the intended task branch is not the default branch, and no extra worktree, stash, untracked file, or unexplained local change overlaps the task. If ownership is unclear, stop.
4. Classify the requested change. For `REPOSITORY_BEHAVIOR`, keep the write set to repository execution policy, tooling, documentation, and explicitly authorized behavior. Product schema/API semantics require a fresh owner authorization.
5. State the accepted seams, validation commands, and stop conditions before editing. Keep secrets, private user data, credentials, generated runtime state, and production data out of the public fork and evidence.

## Validation tier and runtime identity

Before execution, identify the validation tier:

- `T0 STATIC / CHECK`: run the current repository-native pre-commit, `ty`, and diff checks. This makes no Product runtime claim.
- `T1 NEOdb TEST`: use the current source/head and current dependency lock with repository CI-native test conventions. Read the current `.github/workflows/tests.yml` and current Compose/runtime owner authority first; record required database, cache, and search services, cwd, env inputs, and the canonical command before execution. CI search-service identity and admitted runtime identity may differ; do not silently substitute one for the other.
- `T2 OWNER INTEGRATION`: use only exact owner/task-admitted runtime and provider identities and report integration evidence for those identities.

`T1 PASS != T2 PASS`.

The validation application/runtime must correspond to the current task source/head. A stale prior-lane image is not acceptable merely because it starts. Reuse an image only with exact source provenance, dependency compatibility, and claim-compatible runtime identity; when the current Dockerfile supports it, prefer the full task SHA as the build identity. Do not redesign Docker packaging.

Before executing T1 tests, establish and record:

```text
TEST_RUNNER_IDENTITY
RUNNER_PLATFORM
TEST_SOURCE_AVAILABILITY
DEV_TEST_DEPENDENCY_AVAILABILITY
SERVICE_PREREQUISITES
CWD
CANONICAL_TEST_COMMAND
```

The runner platform comes from the current repository-native test authority; do not assume the primary host OS is valid. If current source or test dependencies require Linux APIs such as `fcntl`, native Windows is `PLATFORM_MISMATCH`, not a reason to patch Product source. Do not assume a production/runtime Docker image is a test image: inspect the current `Dockerfile`, `.dockerignore`, and `pyproject.toml` dependency groups before selecting an image. A containerized T1 runner must provide the current task source and test files, current dependency identity, dev/test dependencies, repository-native cwd, and repository-native command. If the production image intentionally excludes test dependencies or test files, use a current-source Linux dev/test-compatible environment; do not discover this through a failed pytest invocation. Continue to read the current workflow authority and declare its required services before execution.

## Baseline versus task-head validation

When a repository-native check fails on the task head, first determine whether the exact clean-base behavior is already authoritative. If it is, use that baseline; otherwise reproduce the exact base in a bounded comparison when needed. Then compare the task head and record:

```text
BASELINE_VALIDATION_SOURCE_SHA
BASELINE_VALIDATION_RESULT
HEAD_VALIDATION_SOURCE_SHA
HEAD_VALIDATION_RESULT
HEAD_DELTA_RESULT = NONE / NEW_REGRESSION / IMPROVED / UNKNOWN
```

A baseline `FAIL` remains `FAIL` or bounded debt. An identical base/head failure is not a new task regression, but it is not a `PASS` merely because the task did not worsen it. A new task-head diagnostic is `NEW_REGRESSION`; if the comparison cannot establish the difference, use `UNKNOWN`. Compare diagnostics semantically (for example, code, path, and message), not only by exit code, count, or line number. Do not expand Product scope to repair unrelated baseline debt.

For repeatable Git/GitHub evidence, identify commits with full 40-character SHAs and identify trees with `git show -s --format=%T HEAD`; quote shell-sensitive revisions. After push, verify the remote branch full SHA, then re-read PR head metadata with a bounded reread. A single stale PR response immediately after push is `REMOTE_METADATA_PROPAGATION_DELAY`, not automatic authority drift; do not add sleep loops.

Fail closed when authority, source identity, ownership, scope, or architecture is uncertain. Do not delegate to subagents unless the task authority explicitly permits it; this repository's default is `SUBAGENTS = PROHIBITED`.

The output is a short preflight record containing authority, repository identity, checkout state, write set, non-scope, planned validation, and any blocker. This skill does not decide Product scope or admit a moving upstream revision.
