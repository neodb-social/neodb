# NeoDB ATProto Implementation

NeoDB can publish a user's marks, reviews and articles (with ratings embedded)
to their ATProto Personal Data Server (PDS) as structured records, in addition
to crossposting a human-readable skeet to Bluesky. This lets other ATProto
applications read a user's NeoDB activity directly from their repository.

The activity lexicon is project-owned under the `net.neodb.*` namespace
(reverse of `neodb.net`), so it is shared by every NeoDB instance. The schema
files live in [`docs/lexicons/net/neodb/`](../lexicons/net/neodb). Long-form
pieces are *also* published under the [standard.site](https://standard.site/)
lexicon; see [Long-form documents](#long-form-documents).

## How it connects

How NeoDB reaches the Atmosphere, alongside its ActivityPub federation:

![How NeoDB instances interconnect](../assets/federation.svg)

## Authentication

Users link their ATProto identity via [OAuth](https://atproto.com/specs/oauth)
(`mastodon/models/bluesky_oauth.py`): the login form takes only a handle, which
is resolved handle -> DID -> PDS -> authorization server; the authorization
request is pushed via PAR with PKCE and the user is redirected to their server
to approve the `atproto transition:generic` scope. NeoDB acts as a confidential
client: the client metadata document is served at
`/account/bluesky/client-metadata.json` (its URL is the `client_id`) and token
requests carry a `private_key_jwt` assertion signed with an auto-generated
ES256 key persisted in SiteConfig. Access tokens are DPoP-bound; a custom
transport for the atproto SDK client signs every XRPC call and transparently
handles server nonce rotation and token refresh. Tokens, the per-session DPoP
key and nonces are stored encrypted on the account. Accounts created before
the OAuth flow keep working through their stored app-password session until
they re-authorize.

## Record types

| Collection (NSID)             | Written from         | Purpose                                   |
| ----------------------------- | -------------------- | ----------------------------------------- |
| `net.neodb.mark`              | a shelf entry        | status (+ optional rating/comment/tags)   |
| `net.neodb.review`            | a review             | long-form review (+ optional rating)      |
| `net.neodb.profile`           | the linked account   | verifiable link to the NeoDB identity     |
| `site.standard.document`      | a review or article  | the long-form piece, for generic readers  |
| `site.standard.publication`   | the linked account   | the owner's journal the documents belong to |

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
  only well-known identifier types (ISBN, CUBN, ASIN, GTIN, ISRC, OCLC,
  MusicBrainz, RSS, IMDB, Steam, Itch, WikiData, TMDB person) qualify;
  site-specific ids stay URL-only via `sources`.

### Rating

A rating is a `net.neodb.defs#rating` object, `{ "value": 1..10, "max": 10 }`,
embedded inline in a mark or review. There is deliberately no standalone
rating record: the value would only duplicate what the mark and review
already carry.

### Profile

`net.neodb.profile` (record key `self`) links the ATProto account to the
owner's NeoDB identity (DID, AP actor id, profile URL, handle), so records
are attributable and the link is verifiable in both directions. It is
modeled on [FEP-c390] identity proofs with the direction mirrored: the
record living in the DID's repo proves the DID side (only the DID holder
can write there), while a [W3C Data Integrity] style `proof` signed with
the identity's RSA federation key (published in the ActivityPub actor
document at `proof.verificationMethod`) proves the NeoDB side. The
cryptosuite `rsa-pkcs1-sha256-jcs` follows the `eddsa-jcs-2022` procedure
with RSA; the signed document includes the `did` so a record cannot be
replayed in another repo. See the lexicon for the exact verification steps.

It is only written while the identity is **publicly discoverable**, deleted
otherwise, and synced on the account refresh path (login and periodic sync)
rather than on crossposting; disconnecting the account removes it.

[FEP-c390]: https://codeberg.org/fediverse/fep/src/branch/main/fep/c390/fep-c390.md
[W3C Data Integrity]: https://www.w3.org/TR/vc-data-integrity/

## Long-form documents

Reviews and articles are additionally published as
[standard.site](https://standard.site/) records: a `site.standard.document`
carrying the piece (markdown, plaintext and cover image) and a
`site.standard.publication` describing the owner's journal (name, summary,
avatar and a theme matching the instance's colors).

### The document -> publication link

A document's `site` points at the publication record by AT-URI. Without
that link the pair reads as a stray page rather than a publication:
bsky.app renders the enhanced article card only when it can resolve a
document to its publication.

A document's canonical URL is the publication's `url` joined with the
document's `path`. The publication's `url` is the owner's profile, so
every piece is served beneath it as well:

```
publication url   https://neodb.social/users/alice
document path                              /article/<uuid>
canonical         https://neodb.social/users/alice/article/<uuid>
```

These user-scoped URLs render the same page as the canonical
`/<type>/<uuid>`, which stays the `rel=canonical` target, and are not
found when the handle does not own the piece. The crossposted skeet's
external embed points at the user-scoped URL, so the embed, the document
and the page all name one URL.

An owner who is not publicly discoverable has no publication record; their
documents stay loose, with `site` set to the instance URL so the same
`path` joins to `/<type>/<uuid>`.

### Discovery and verification

The records name web pages, and the pages name the records back:

- `/users/<handle>/.well-known/site.standard.publication` returns the
  publication's AT-URI as plain text, served under the record's own `url`.
- Piece pages carry `<link rel="site.standard.document">` and
  `<link rel="site.standard.publication">` holding the matching AT-URIs.

### Crosspost wiring

The skeet's external embed strong-refs both records, publication first.
The document is written before the skeet so the refs exist, then written
again afterwards to add `bskyPostRef` pointing back at the skeet, which
keeps off-platform comments discoverable. That second write changes the
document's CID, so the CID in the embed goes stale by design: the refs are
resolved by URI, and bsky.app keeps the original snapshot rather than
re-rendering edited records.

## Record keys

Every record is keyed by the mark's or review's own uuid, which is
deterministic and derivable from the item itself:

```
at://<did>/net.neodb.mark/<mark-uuid>
at://<did>/net.neodb.review/<review-uuid>
```

Keying by the mark/review rather than the subject item keeps the AT-URI stable
across catalog item merges, and lets distinct items (e.g. multiple reviews of
one work) map to distinct records.

The `site.standard.*` records use [TID](https://atproto.com/specs/tid) keys instead, as that lexicon
requires. The key is frozen on first write, so later backdating cannot
orphan the record.

Records are reconciled idempotently against the PDS: each record that should
exist is written by key (overwriting in place on edit), and any record that
should no longer exist is deleted.

## Fediverse back-reference

Bluesky skeet (`app.bsky.feed.post`) carries an off-lexicon `neodbOriginalUrl`
field pointing back to the ActivityPub post URL.

## When records are published

Records are reconciled on the same path as Bluesky crossposting, so they
require a linked Bluesky/ATProto account and are only written for **public**
pieces (PDS records are world-readable). When a piece's visibility leaves
public, or the piece is deleted, its records are removed.

## Lexicon publication

The schema is published as a `com.atproto.lexicon.schema` record under
`@neodb.net`, with a DNS TXT record at `_lexicon.neodb.net` pointing to its DID,
so the canonical `net.neodb.*` lexicon is resolvable from the network.
