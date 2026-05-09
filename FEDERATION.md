# Federation

see [doc](https://neodb.net/internals/federation) for FEP-67ff related information.

## Shelf / Collection federation

NeoDB ships two list-of-items models: `Collection` (user-curated) and
`Shelf` (per-status: wishlist / progress / complete / dropped). They
both federate using the same AP wire shape — type `"Shelf"` — sharing
serialization, dispatch, and inbound sync infrastructure on the abstract
`List` base in `journal/models/itemlist.py`. The wire type is
deliberately *not* `Collection` because the AS standard reserves that
name for generic AP collections; an AP-aware peer hitting the same URL
must not be able to confuse a NeoDB list-of-items with a real AS
`Collection`.

The shape mirrors BookWyrm (`bookwyrm/activitypub/ordered_collection.py`,
specifically `Shelf`/`BookList`/`OrderedCollectionPage`). NeoDB keeps
its existing Note-Post announcement layer (Mastodon timeline visibility)
on top of the BookWyrm-style AP shape.

Federation is two-step:

1. **Announcement Note** (Collection only) — when a Collection is saved,
   NeoDB publishes a Takahe Note Post whose `relatedWith[0]` carries a
   *lightweight* Shelf envelope: `id`, `type`, `name`, `content`,
   `mediaType`, `attributedTo`, `published`, `updated`, `href`,
   `totalItems`, `first`, `last`. The ordered member list is **not**
   inline — receivers follow `first`/`last` to the items endpoint.
2. **Dereferenceable AP endpoint** — the list's `id` URL
   (`/collection/<uuid>` for Collection, `/users/<handle>/shelf/<status>`
   for Shelf) content-negotiates: HTML for browsers,
   `application/activity+json` for AP clients.

NeoDB Shelf does **not** push announcement Notes — per-mark Status
posts already cover activity, and a per-Shelf Note would balloon
volume. Shelves are discovered via URL paste, peer follow, or direct
AP fetch.

### Lightweight Shelf envelope (in the Note Post and at the dereferenceable endpoint)

```json
{
  "id":          "https://server/collection/<uuid>",
  "type":        "Shelf",
  "name":        "<title or shelf label>",
  "content":     "<brief markdown — Collection only>",
  "mediaType":   "text/markdown",
  "attributedTo": "<actor uri>",
  "published":   "...",
  "updated":     "...",
  "totalItems":  <int>,
  "first":       "https://server/.../items?page=1",
  "last":        "https://server/.../items?page=K",

  // Optional, NeoDB-specific:
  "shelfType":   "wishlist|progress|complete|dropped",   // Shelf only
  "query":       "<JournalQueryParser query>"            // dynamic Collection only
}
```

`shelfType` is the inbound dispatcher discriminator: present →
NeoDB Shelf; absent → NeoDB Collection (see
`takahe.ap_handlers._ShelfDispatcher`).

### Items endpoint — paginated `OrderedCollection`

Each list exposes `<id>/items`:

- **No `?page` param** returns the AS-standard `OrderedCollection`
  envelope (`id`, `type`, `totalItems`, `first`, `last`).
- **`?page=N`** returns one `OrderedCollectionPage` slice with
  `id`, `partOf`, `orderedItems`, plus `next`/`prev` URLs as
  applicable.

```json
// /collection/<uuid>/items
{
  "id":         "https://server/collection/<uuid>/items",
  "type":       "OrderedCollection",
  "totalItems": <int>,
  "first":      "https://server/collection/<uuid>/items?page=1",
  "last":       "https://server/collection/<uuid>/items?page=K"
}

// /collection/<uuid>/items?page=2
{
  "id":           "https://server/collection/<uuid>/items?page=2",
  "type":         "OrderedCollectionPage",
  "partOf":       "https://server/collection/<uuid>/items",
  "next":         "https://server/collection/<uuid>/items?page=3",
  "prev":         "https://server/collection/<uuid>/items?page=1",
  "orderedItems": [ ... ShelfItem entries ... ]
}
```

Per-page cap: `AP_PAGE_SIZE = 100` (`journal/models/itemlist.py`).
Total list size is unbounded — receivers follow `next` until
exhausted (with `MAX_PAGES = 1000` defense-in-depth in
`journal/jobs/list_sync.py`).

### `ShelfItem` entry shape

A single shared per-entry shape covers both Shelf and Collection:

```json
{
  "type":        "ShelfItem",
  "withRegardTo": "https://server/book/<uuid>",       // catalog item AP URL — always
  "post":         "https://server/@alice/123",        // Shelf only — mark's latest Status post
  "commentText":  "..."                               // Collection only — per-list note
}
```

`withRegardTo` is the catalog item URL. Following it returns one of
the AP catalog item types (`Edition`, `Movie`, `TVShow`, `Album`, ...).

`post` is included when a `ShelfMember`'s associated mark has a
`latest_post` — receivers can chain a single signed GET into the
existing `_post_fetched` flow to ingest the corresponding Mark +
Comment + Rating + Review for free, without first having to follow
the user.

`commentText` (renamed from the pre-rename `note` to avoid collision
with the AS `Note` type) is the user's per-collection annotation.

### HTTP signatures

Both sides use a fixed canonical shape (`takahe/auth.py`):

- Algorithm: `rsa-sha256` only.
- Signed headers: exactly `(request-target) host date`.
- `Date` required; 300s skew window.
- No body / no digest (GET only).

The verifier resolves the keyId actor via `takahe.models.Identity`
(must already be cached locally — no synchronous outbound fetch on
verify), then maps it to a NeoDB `APIdentity` via
`Takahe.get_or_create_remote_apidentity`. The AP views
(`_list_ap_object_view` and `_list_items_view` in
`journal/views/collection.py`) make signature verification
**optional**: present-and-valid signatures resolve to the signer's
`APIdentity`; absent signatures resolve to anonymous; invalid
signatures return 401. Authorization is then delegated to
`is_visible_to_identity`, which lets public lists through to
anonymous callers (matching the HTML route) and gates followers-only
/ private lists on the caller's actor following / owning the list.

The signer (`takahe.auth.sign_get`) signs as Takahe's SystemActor
(keys in the shared `takahe_config` table, generated by Takahe's
standard `generate_keys_if_needed` bootstrap).

### SSRF gates

Five validation points block the worker from being driven against
internal infrastructure by a malicious peer
(`common.validators.is_valid_url` rejects malformed, non-HTTP(S),
and private / loopback / link-local / reserved IPs):

1. `List.update_by_ap_envelope` validates the inbound `id` URL
   (`remote_id`) at persist time and requires its host to match the
   announcing author's actor host.
2. `enqueue_fetch` (catalog gateway for all async URL fetches)
   validates before queuing a job.
3. `sign_get` validates each outbound URL (envelope + every
   `next` page URL the page-walker follows).
4. `Item.get_by_ap_object` validates remote item URLs before
   `SiteManager` issues synchronous HTTP.
5. `journal.jobs.list_sync._signed_get_json` re-validates each page
   URL before signing/issuing the GET.

### Inbound flow

1. Note arrives via the regular Takahe inbox path. Dispatcher
   (`takahe/ap_handlers.py:_post_fetched`) detects a `"Shelf"`
   piece in `relatedWith` and calls
   `_ShelfDispatcher.update_by_ap_object`, which routes to NeoDB
   Shelf or NeoDB Collection based on the envelope's `shelfType`.
2. The mirror is upserted with `local=False`, `remote_id=<id>`,
   `visibility=Takahe.visibility_t2n(post.visibility)`. No members yet.
3. `List.update_by_ap_envelope` enqueues
   `journal.jobs.list_sync.fetch_remote_list_members` (passing the
   dotted class path so the same job module serves both Collection
   and Shelf).
4. The job calls `sign_get(remote_id)` to fetch the envelope, then
   walks the items endpoint via `first`→`next` until exhausted
   (bounded by `MAX_PAGES`), accumulates `orderedItems` entries, and
   feeds them through `cls._sync_members_from_ap` for atomic upsert
   under `select_for_update`. Items not yet in the local catalog are
   queued via `enqueue_fetch`; if anything was pending the job
   reschedules itself (bounded retries via `MAX_FETCH_ATTEMPTS = 3`).

### URL paste

Pasting a remote Collection or Shelf URL
(`catalog/views/search.py:_maybe_remote_piece`) matches by
`remote_id` against indexed Collection / Shelf / Review / Note
tables. On a hit *and* `is_visible_to_identity` passes for the
requesting user, the view enqueues `fetch_remote_list_members` for
list types and 302s to the local mirror. Misses fall through to the
existing catalog fetch path.

### Authorization model

- **Delivery**: Takahe enforces AP visibility on outbound fanout
  (followers-only Notes only reach follower inboxes).
- **Storage**: receiver persists `visibility` from the Note
  unchanged; trust comes from "Takahe gave us this post."
- **Display (HTML view)**: Django session auth + `is_visible_to(user)`
  — remote followers visiting the link as anonymous browser users
  see only public lists via the HTML route. Owners always see their
  own content even if `user.identity` is unpopulated (rare:
  identity deletion, mid-signup) — see
  `journal/models/mixins.py`.
- **Display (AP view)**: HTTP signature verification +
  `is_visible_to_identity(remote_apidentity)` — a remote follower's
  Takahe-signed GET is authenticated as that actor, so they see
  follower-only lists they're entitled to.
- **Mutating endpoints**: rejected for `local=False` mirrors via
  the existing owner-vs-`request.user.identity` check.

### Local catalog gating

`Collection.save` only auto-creates a `CatalogCollection` row when
`self.local` (`journal/migrations/0011_collection_catalog_item_nullable`
makes the field nullable). Remote mirrors don't get stub catalog
entries.

### AP endpoints refuse mirrors

`_list_ap_object_view` and `_list_items_view`
(`journal/views/collection.py`) gate on `_is_locally_owned(instance)`
and 404 otherwise. The origin server is authoritative for their own
users' lists; we hold a possibly-stale snapshot. The check requires
both `instance.local` AND `instance.owner.local` because
`ShelfManager` auto-initializes a `Shelf` row per `shelf_type` for any
APIdentity it touches (local OR remote, with `local=True` from the
model default), so a remote owner's auto-initialized shelf row would
otherwise sneak through. Peers chasing a federated link should follow
the original `id` URL on the origin instead.

### Known limitation: followers-only inbound list sync

The page-walking inbound sync signs every fetch as the local Takahe
SystemActor (`takahe.auth.sign_get`). On the receiving (origin) side,
`is_visible_to_identity` is given that signing actor and asks "is this
actor a follower of the owner?" — for a SystemActor, the answer is
always **no**, so the origin returns 404. This means
**followers-only Shelf / Collection items endpoints cannot be synced
between peers in this PR**. Only public lists round-trip correctly.

Public lists work because `is_visible_to_identity(visibility=0)`
returns True regardless of the signer (`anonymous_viewable` path).

Fixing this requires either signing as a local follower of the remote
owner (we'd need to pick one and use their key — Takahe stores keys
in `takahe.Identity.private_key`) or relaxing the receiver-side check
to accept "instance SystemActor of an instance with at least one
follower" (Mastodon's per-instance trust model). Both are non-trivial
auth-model changes; they're left out of this PR. The announcement
Note still delivers the lightweight envelope to followers, so a
follower's local mirror is created with `visibility` set; only the
member list lags.

### Out of scope (deferred)

- **`Tag` federation** — column provisioned (`Tag.remote_id` via the
  abstract `List` base) but no inbound/outbound pipeline yet.
- **Collaborative collections** (`Collection.collaborative=1`) — local-only.
- **Synchronous actor fetch on signature verify** — currently the
  verifier rejects unknown signers rather than fetching their actor
  doc, to avoid letting unsigned probes drive outbound HTTP.
  Federation push primes the cache through normal channels.
- **Refetch-by-URL for Review / Note** — paste resolves to existing
  mirror, but no analogous `fetch_remote_list_members` exists for
  those types.
- **Mark/Comment/Rating/Review ingestion via `ShelfItem.post` URL**
  — wire field is emitted but receivers don't currently chain a
  signed-GET to ingest the side data. The hook is documented; the
  follow-up trivially feeds the URL into the existing
  `_post_fetched` flow.
- **Pushed updates on member change for Shelf** — receivers see
  Shelf changes only on next URL fetch / paste. Add a Shelf-level
  Update activity if/when a UI surfaces "follow this shelf."
