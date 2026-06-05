# NeoDB ATProto Lexicon

NeoDB can publish a user's marks and reviews (with ratings embedded) to their
ATProto Personal Data Server (PDS) as structured records, in addition to
crossposting a human-readable skeet to Bluesky. This lets other ATProto
applications read a user's NeoDB activity directly from their repository.

The lexicon is project-owned under the `net.neodb.*` namespace (reverse of
`neodb.net`), so it is shared by every NeoDB instance. The schema files live in
[`docs/lexicons/net/neodb/`](../lexicons/net/neodb).

## Record types

| Collection (NSID)  | Written from         | Purpose                                   |
| ------------------ | -------------------- | ----------------------------------------- |
| `net.neodb.mark`   | a shelf entry        | status (+ optional rating/comment/tags)   |
| `net.neodb.review` | a review             | long-form review (+ optional rating)      |

### Subject

NeoDB catalog items are not themselves ATProto records, so a work cannot be
referenced with a `com.atproto.repo.strongRef`. Instead every record embeds a
`net.neodb.defs#subject` describing the work inline:

```json
{
  "uri": "https://neodb.social/tv/season/abc123",
  "category": "tv",
  "type": "TVSeason",
  "title": "Shogun Season 1",
  "cover": "https://neodb.social/m/item/.../cover.jpg",
  "sources": ["https://www.themoviedb.org/tv/12345/season/1"],
  "identifiers": [{ "type": "imdb", "value": "tt2788316" }]
}
```

- `uri` is the item's permalink on the originating instance.
- `category` is the broad media category (`book`, `movie`, `tv`, `music`,
  `game`, `podcast`, `performance`, `people`) -- declared as an open set
  (`knownValues`) so future categories do not break validation.
- `type` is the specific NeoDB item class (same vocabulary as the NeoDB API
  schema), so entities that share a category stay distinguishable —
  `TVShow` / `TVSeason` / `TVEpisode`, `Podcast` / `PodcastEpisode`,
  `Performance` / `PerformanceProduction`, plus `Edition`, `Movie`, `Album`,
  `Game`.
- `cover` is included only when the item has a non-default cover.
- `sources` lists the external source records (IMDB, TMDB, Douban, Goodreads,
  ...) the work was matched from, **referenced by URL, not raw id**, for
  cross-instance matching.
- `identifiers` additionally lists **standardized identifiers** of the work --
  only types from `IdealIdTypes` (ISBN, CUBN, ASIN, GTIN, ISRC, OCLC,
  MusicBrainz, RSS, IMDB, Steam, Itch, WikiData, TMDB person) qualify;
  site-specific ids stay URL-only via `sources`.

### Rating

A rating is a `net.neodb.defs#rating` object, `{ "value": 1..10, "max": 10 }`,
embedded inline in a mark or review. There is deliberately no standalone
rating record: the value would only duplicate what the mark and review
already carry.

## Record keys and statelessness

Every record is keyed by the journal **piece's own uuid** (the mark's or the
review's), which is deterministic and derivable from the piece itself:

```
at://<did>/net.neodb.mark/<mark-uuid>
at://<did>/net.neodb.review/<review-uuid>
```

Keying by the piece rather than the subject item keeps the AT-URI stable
across catalog item merges, and lets distinct pieces (e.g. multiple reviews
of one work, if allowed in the future) map to distinct records.

Because the key is derivable, NeoDB stores **no** record bookkeeping in its
database. On every sync the relevant piece is reconciled against the PDS:

- `put_record` (idempotent by key) writes each record that should currently
  exist, overwriting in place on edit;
- `delete_record` (idempotent; no error if absent) removes any managed
  collection that should not exist -- e.g. every record when a piece is made
  non-public or deleted.

`Piece.atproto_collections()` declares which collections a piece manages and
`Piece.to_atproto_records()` returns the records that should exist now;
`Piece._sync_records_to_bluesky()` performs the reconciliation.

## When records are published

Records are reconciled on the same path as Bluesky crossposting
(`Piece.sync_to_bluesky`), so they require a linked Bluesky/ATProto account and
are only written for **public** pieces (PDS records are world-readable). When a
piece's visibility leaves public, its records are deleted. When a piece is
deleted, its records are removed by the async crosspost-deletion job.

## Publishing the lexicon

Publishing the schemas is **not required** for writes to work: a PDS accepts
records in collections it does not know without validating them. The JSON
files in this repository are the authoritative spec for now.

To make `net.neodb.*` resolvable -- so tools, appviews and other developers can
discover and validate the schemas -- ATProto lexicon resolution needs:

1. a project-controlled ATProto account (the official `@neodb.net` account);
2. each schema JSON published as a `com.atproto.lexicon.schema` record in that
   account's repo, with the record key set to the NSID (e.g. `net.neodb.mark`);
3. a DNS TXT record at `_lexicon.neodb.net` pointing to the account's DID
   (`did=did:plc:...`).

Step 2 is automated: the `publish lexicons` GitHub Actions workflow runs on
every push to `main` of `neodb-social/neodb` that touches `docs/lexicons/`,
publishing via the standalone script [`docs/lexicons/publish.py`](../lexicons/publish.py)
(no Django or database required). Publishing is an explicit opt-in: the
workflow skips the publish step until the `ATPROTO_APP_PASSWORD` repository
secret (an app password for the `@neodb.net` account) is configured. The
script also verifies step 3 and prints the exact TXT record to add when
missing.

To run it manually:

```
ATPROTO_APP_PASSWORD=... uv run docs/lexicons/publish.py --handle neodb.net
```

### Updating a published lexicon

Publishing reruns automatically on merge to `main` whenever the schema files
change; `put_record` overwrites the records in place (same rkey), so
resolution always serves the latest files from this repository.

Schema evolution must be **backwards-compatible**: adding new optional fields
is fine; never remove or rename fields, change a field's type, make an
optional field required, or tighten constraints. Records already written to
user repos are not rewritten when the lexicon changes -- they refresh to the
current shape on the owner's next sync of that piece (stable rkey, put
overwrites) -- so old-shape records remain readable in the meantime and must
stay valid. A change that cannot be made compatibly needs a new NSID (e.g.
`net.neodb.mark2`) rather than a breaking edit. Note that once the lexicon is
published and resolvable, PDS hosts may start validating new writes against
it, so the published schemas must always match what the code writes.
