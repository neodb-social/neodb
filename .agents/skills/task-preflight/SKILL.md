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

Fail closed when authority, source identity, ownership, scope, or architecture is uncertain. Do not delegate to subagents unless the task authority explicitly permits it; this repository's default is `SUBAGENTS = PROHIBITED`.

The output is a short preflight record containing authority, repository identity, checkout state, write set, non-scope, planned validation, and any blocker. This skill does not decide Product scope or admit a moving upstream revision.
