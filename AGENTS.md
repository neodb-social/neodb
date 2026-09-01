# VinylHub repository overlay

This file adds only durable VinylHub execution rules. NeoDB's native Python, Django, migration, Docker, test, formatting, and release conventions remain authoritative unless a linked task explicitly authorizes a bounded change.

## Authority and scope

- App program Issue/contract -> local owner Issue -> one task branch -> one owner PR -> Controller review -> app integration gate.
- Fresh-read the current handoff, app contract, owner Issue, root guidance, default branch, and fork/upstream identity before non-trivial work. Volatile scope, SHAs, runtime versions, and acceptance decisions belong to those current sources and the owner PR, not this file.
- M0/bootstrap work is `REPOSITORY_BEHAVIOR`: repository policy, narrow Skills, evidence, and source/runtime admission only. Fail closed before Product schema/API semantics, identity changes, external integrations, generic frameworks, ownership changes, or production cutover.

## Repository safety

- One repository has one writer by default. Codex Sub Agents are prohibited unless the current authority explicitly changes that rule.
- Do not write non-trivial changes directly to the default branch. Use one task branch, preserve upstream history and branch naming, and deliver one coherent owner PR with semantic commits/merge review.
- Public fork content must contain no secrets, credentials, private user data, database dumps, private media, or uncontrolled runtime output. Use test-only values and isolated disposable services for validation.
- Keep evidence reproducible and minimal: exact source/tree identity, changed paths, commands, outcomes, scope/non-changes, and bounded unknowns. Never turn an unavailable check into a pass.

## Upstream and delivery identity

- Treat `upstream/main` as a tracking candidate. Do not silently follow moving upstream or rewrite upstream history.
- An admitted baseline is an exact reviewed upstream commit and tree recorded in the owner PR. VinylHub delivery/release identity is downstream and must point to its own reviewed commit and, when applicable, immutable tag/image digest; never reuse or retag an upstream release identity.

Use the repository-local Skills at `.agents/skills/<skill>/SKILL.md` for the repeatable HOW: `task-preflight`, `architecture-conformance`, `acceptance-evidence`, and `delivery-lifecycle`. `.codex/` remains tool-private/local state unless a future owner contract gives it repository meaning. Skills do not grant scope, architecture authority, or acceptance.
