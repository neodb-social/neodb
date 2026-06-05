# NeoDB ATProto Implementation

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

Schema is being published as a `com.atproto.lexicon.schema` record in `@neodb.net`,
with a DNS TXT record at `_lexicon.neodb.net` pointing its DID.

To publish manually:

```
ATPROTO_APP_PASSWORD=... uv run docs/lexicons/publish.py --handle neodb.net
```

Or automatically on merge to `main` whenever the schema files change.
