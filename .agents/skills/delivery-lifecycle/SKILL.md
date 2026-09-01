---
name: delivery-lifecycle
description: Deliver authorized NeoDB repository work through one task branch, one coherent owner PR, validation, and controller review.
---

# Delivery lifecycle

Use this skill for changes that are authorized for delivery in this repository.

1. Start from a fresh-read default branch and exact upstream comparison. Create one task branch; never write non-trivial changes directly to the default branch.
2. Keep one repository lane and one writer. Make the smallest coherent change set, preserve upstream history and branch naming, and use semantic commits/merges that explain the repository transition.
3. Validate the changed behavior using native commands and capture the acceptance evidence. Recheck the diff for secrets, private data, generated state, scope creep, and hidden Product semantics.
4. Open one owner-repository PR that links the local owner Issue and the app contract. Its body records reviewed head, source/tree identity, validation, non-changes, and any bounded unknowns.
5. Stop at Controller review and the app integration gate. Do not self-merge, cut over production, or schedule downstream feature work from an unadmitted baseline.

Upstream `main` is a tracking candidate, not an implicit VinylHub release. A VinylHub delivery/release identity must point to its own reviewed downstream commit (and later, if applicable, immutable tag/image digest) without retagging or rewriting upstream history.

Fail closed on direct-default writes, unexpected upstream drift, dirty ownership, missing linked authority, unresolved validation, architecture conflict, or any secret/private-data exposure.
