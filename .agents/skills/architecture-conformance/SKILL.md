---
name: architecture-conformance
description: Qualify NeoDB changes against its existing Django, Takahē, persistence, queue, search, and runtime seams without inventing Product architecture.
---

# Architecture conformance

Use this skill when a task touches NeoDB boundaries, runtime behavior, migrations, or a proposed integration seam.

- Start from the current upstream-traceable tree and inspect the nearest existing app, model, API, migration, job, and Compose entrypoint. Reuse native boundaries and naming.
- Qualify only the seam named by the task. For the admitted Product-backend baseline, inspect User/session/account, APIdentity/Takahē, Journal Review/Shelf/Mark/Collection, SocialAccount/MastodonAccount, PostgreSQL migrations, RQ/Redis, Typesense, and Docker/runtime wiring.
- Record whether the seam is present, how it is exercised, and whether the task introduces any delta. Existing upstream `CollectionItem` code is baseline context, not permission for new Product feature work.
- Preserve the split between the `neodb` Django project and the adjacent `takahe` project, including their database and task-queue conventions. Use `neodb-init` for schema initialization as documented upstream.

Fail closed if the requested work needs a new owner, generic framework/provider graph, Product schema/API semantics, identity model, external integration, migration policy, or deployment architecture not already authorized. Do not hide an architecture change inside bootstrap documentation or tooling.

The output is a bounded seam qualification: paths inspected, native convention preserved, observed change (if any), validation needed, and unresolved architecture questions. This skill does not grant Product scope or acceptance authority.
