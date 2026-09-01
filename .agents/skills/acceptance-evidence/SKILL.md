---
name: acceptance-evidence
description: Produce a compact, reproducible evidence packet for NeoDB owner delivery and upstream-baseline admission.
---

# Acceptance evidence

Use this skill when preparing review evidence for a repository task or an upstream-traceable baseline.

Capture evidence in this order:

1. Authority: app contract, owner Issue, PR, change classification, write set, and non-scope.
2. Identity: fork and upstream URLs, default branch, exact source commit, tree identity, and any separate VinylHub delivery identity.
3. Diff: changed paths, clean/dirty state, and a statement that no Product feature schema/API or private data was added.
4. Validation: exact commands and outcomes for native pre-commit/configuration checks, Django startup/system checks, migration checks or `neodb-init` smoke, targeted tests, and runtime/Compose checks. Distinguish PASS, FAIL, and NOT RUN with the reason.
5. Review notes: accepted seams, baseline drift, remaining unknowns, and the next integration gate.

Each evidence packet must identify, where applicable:

```text
VALIDATION_TIER
VALIDATION_SOURCE_SHA
VALIDATION_SOURCE_TREE
APPLICATION_RUNTIME_IDENTITY
DEPENDENCY_IDENTITY
SERVICE_PREREQUISITES
TEST_RUNNER_IDENTITY
RUNNER_PLATFORM
BASELINE_VALIDATION_SOURCE_SHA
BASELINE_VALIDATION_RESULT
HEAD_VALIDATION_SOURCE_SHA
HEAD_VALIDATION_RESULT
HEAD_DELTA_RESULT
COMMAND
CWD
EXIT
RESULT
```

`RESULT` must be one of `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED`, or `UNKNOWN`. Keep validation claims at their observed tier: never promote `T0` to `T1`, `T1` to `T2`, mock to live, or an old image to a current-head runtime. `T1 PASS != T2 PASS`.

For any baseline-versus-task-head comparison, report both source SHAs and validation results. `HEAD_DELTA_RESULT` must be one of `NONE`, `NEW_REGRESSION`, `IMPROVED`, or `UNKNOWN`; compare diagnostic semantics rather than treating matching exit codes or counts as proof. A baseline `FAIL` remains `FAIL` or bounded debt and must not be promoted to `PASS`.

Run commands with test-only values or isolated local services. Redact credentials and do not include secrets, tokens, private user data, database dumps, media, logs containing personal data, or uncontrolled environment output. Evidence must support replay without becoming a second source of Product requirements.

Fail closed when a required acceptance fact cannot be tied to an exact command, source identity, or authorized scope. Do not convert a static inspection into runtime approval or claim a clean result after an unrecorded failure.
