# Configuration

## Important settings in `.env`

Set these in `.env` before starting the instance for the first time:

 - `NEODB_SECRET_KEY` - 50 characters of random string, no white space
 - `NEODB_SITE_DOMAIN` - the domain name of your site

**`NEODB_SECRET_KEY` and `NEODB_SITE_DOMAIN` MUST NOT be changed later.**

If you are debugging or developing:

 - `NEODB_DEBUG` - True will turn on debug for both neodb and takahe, turn off relay, and reveal self as debug mode in nodeinfo (so peers won't try to run fedi search on this node)
 - `NEODB_IMAGE` - the docker image to use, `neodb/neodb:edge` for the main branch

## Site Settings UI

Most configuration settings can be managed through the web-based Site Settings page at `/manage/`, accessible to superusers. This includes:

 - **Branding** - site name, logo, icon, color theme, description, footer links, custom HTML head
 - **Discover** - minimum marks, update interval, language filtering, local-only mode, popular posts/tags
 - **Access** - invite-only mode, local-only posting, email domain blocklist, email delivery, Mastodon/Bluesky/Threads login, default and preferred languages
 - **Federation** - default relay, fanout limit, prune horizon, search sites/peers, hidden categories
 - **API Keys** - Spotify, TMDB, Google Books, Discogs, IGDB, Steam, DeepL, LibreTranslate, Threads, Discord webhooks
 - **Downloader** - scraping providers, proxy list, provider API keys, timeouts
 - **Advanced** - alternative domains, Mastodon client scope, cron jobs, index aliases
 - **Environment** - read-only view of the settings that come from `.env` and cannot be changed in the UI (see below), plus any other `NEODB_*` and `TAKAHE_*` variables the process received. Passwords and keys are masked.

Settings configured in the UI take effect immediately (within 30 seconds) without restarting the server. Values set in the UI override `.env` values. If a setting has not been configured in the UI, the `.env` value is used as fallback.

Mastodon login is enabled by default. It can be disabled in Site Settings > Access without affecting Mastodon accounts already linked to signed-in users.

Before creating the first admin, configure `NEODB_EMAIL_URL` and `NEODB_EMAIL_FROM` in `.env` so the account can receive its login code. After an admin is available, email delivery can be managed in Site Settings > Access. A database value takes priority over the bootstrap `.env` value. Supported email URL formats include:

 - `smtp://<username>:<password>@<host>:<port>`
 - `smtp+tls://<username>:<password>@<host>:<port>`
 - `smtp+ssl://<username>:<password>@<host>:<port>`
 - `anymail://<anymail_backend_name>?<anymail_args>`, see [anymail doc](https://anymail.dev/)

## Settings that must remain in `.env`

These settings require infrastructure access or process restart and cannot be managed from the UI. Those that reach the application process are shown, with credentials masked, in Site Settings > Environment; `NEODB_DATA`, `NEODB_PORT` and `NEODB_IMAGE` are consumed by Docker Compose itself and do not appear there.

 - `NEODB_SECRET_KEY` - Django secret key
 - `NEODB_SITE_DOMAIN` - primary domain (identity-critical)
 - `NEODB_DB_URL`, `TAKAHE_DB_URL` - database connection strings
 - `NEODB_REDIS_URL` - Redis URL for cache and job queue
 - `NEODB_SEARCH_URL` - Typesense search backend URL
 - `MEDIA_BACKEND` - storage backend (local/s3)
 - `MEDIA_BACKEND_S3_ACL`, `MEDIA_BACKEND_S3_REGION`, `MEDIA_BACKEND_S3_ADDRESSING_STYLE`, `MEDIA_BACKEND_S3_PUBLIC_ENDPOINT` - optional S3 options, see [storage](storage.md)
 - `NEODB_MEDIA_ROOT`, `NEODB_MEDIA_URL` - media storage paths
 - `SSL_ONLY` - Force HTTPS
 - `NEODB_DATA` - data directory for docker volumes (database, redis, typesense, media), default `../data`
 - `NEODB_PORT` - the port to expose the main web server on
 - `NEODB_IMAGE` - docker image to pull from
 - `TAKAHE_NO_FEDERATION` - disable federation (test/development only)
 - `NEODB_SENTRY_DSN`, `NEODB_SENTRY_SAMPLE_RATE` - Sentry error reporting for NeoDB. Requires restart.
 - `TAKAHE_SENTRY_DSN` - Sentry DSN for takahe container
 - `NEODB_ADMIN_HANDLES` - comma-separated list of handles to auto-promote to superuser on registration, in `type:handle` format (e.g. `mastodon:user@mastodon.social,email:admin@example.com`). Supported types: `mastodon`, `email`, `bluesky`, `threads`.
 - `NEODB_LOG_LEVEL` - logging level (DEBUG, INFO, WARNING, ERROR). Requires restart.


## Media Storage

`MEDIA_BACKEND` selects where NeoDB keeps the files which users upload. See [storage](storage.md) for S3 and for the S3-compatible servers which you can run yourself.


## Scaling Parameters

For a high-traffic instance, raise these settings to higher values in `.env`, as long as the host server can handle them:

 - `NEODB_WEB_WORKER_NUM`
 - `NEODB_API_WORKER_NUM`
 - `NEODB_RQ_WORKER_NUM`
 - `TAKAHE_WEB_WORKER_NUM`
 - `TAKAHE_STATOR_CONCURRENCY`
 - `TAKAHE_STATOR_CONCURRENCY_PER_MODEL`

Further scaling up with multiple nodes (e.g. via Kubernetes) is beyond the scope of this document, but consider running db/redis/typesense separately, and then duplicating web/worker/stator containers as long as connections and mounts are properly configured; `migration` only runs once on start or upgrade, and it should be kept that way.


## Other Maintenance Tasks

Add alias to your shell for easier access. Not necessary, just for convenience.

```
alias neodb-manage='docker compose --profile production run --rm shell neodb-manage'
```

Manage user tasks and cron jobs

```
neodb-manage task --list
neodb-manage cron --list
```

Rebuild search index

```
neodb-manage catalog idx-rebuild
```

There are [more commands](usage/catalog.md) available to manage the catalog; also take a look at [Manage Accounts](accounts.md) to learn how to create an admin/staff account, create an invitation code and more.


## Run PostgreSQL/Redis/Typesense without Docker

It's currently possible but quite cumbersome to run without Docker, hence not recommended. However, it's possible to only use docker to run neodb server but reuse existing PostgreSQL/Redis/Typesense servers with `compose.override.yml`, an example for reference:

```
services:
  redis:
    profiles: ['disabled']
  typesense:
    profiles: ['disabled']
  neodb-db:
    profiles: ['disabled']
  takahe-db:
    profiles: ['disabled']
  migration:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  neodb-web:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
    healthcheck: !reset {}
  neodb-web-api:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
    healthcheck: !reset {}
  neodb-worker:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  neodb-worker-extra:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  takahe-web:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  takahe-stator:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  shell:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  root:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  dev-neodb-web:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  dev-neodb-worker:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  dev-takahe-web:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  dev-takahe-stator:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  dev-shell:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
  dev-root:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on: !reset []
```
(`extra_hosts` is only needed if PostgreSQL/Redis/Typesense is on your host server)


## Multiple instances on one server

It's possible to run multiple clusters in one host server with docker compose, as long as `NEODB_SITE_DOMAIN`, `NEODB_PORT` and `NEODB_DATA` are different.


## Deprecated `.env` settings

The following settings can still be set in `.env` for bootstrap or backward-compatible defaults, but should normally be configured through the Site Settings UI (`/manage/`). A database value overrides the `.env` value.

### Customization
 - `NEODB_SITE_LOGO`
 - `NEODB_SITE_ICON`
 - `NEODB_SITE_NAME`
 - `NEODB_USER_ICON`
 - `NEODB_SITE_COLOR`
 - `NEODB_SITE_INTRO`
 - `NEODB_SITE_HEAD`
 - `NEODB_SITE_DESCRIPTION`
 - `NEODB_SITE_LINKS`
 - `NEODB_PREFERRED_LANGUAGES` (sets both *Preferred Languages* and the initial *Default Language*, which are separate settings in the UI)
 - `NEODB_ALTERNATIVE_DOMAINS`
 - `NEODB_INVITE_ONLY`
 - `NEODB_ENABLE_LOCAL_ONLY`
 - `NEODB_LOGIN_MASTODON_WHITELIST`
 - `NEODB_ENABLE_LOGIN_BLUESKY`
 - `NEODB_ENABLE_LOGIN_THREADS`

### Email
 - `NEODB_EMAIL_URL`
 - `NEODB_EMAIL_FROM`

### Discover
 - `NEODB_DISCOVER_FILTER_LANGUAGE`
 - `NEODB_DISCOVER_SHOW_LOCAL_ONLY`
 - `NEODB_DISCOVER_UPDATE_INTERVAL`
 - `NEODB_DISCOVER_SHOW_POPULAR_POSTS`
 - `NEODB_DISCOVER_SHOW_POPULAR_TAGS`
 - `NEODB_MIN_MARKS_FOR_DISCOVER`

### Federation
 - `NEODB_DISABLE_DEFAULT_RELAY`
 - `NEODB_SEARCH_PEERS`
 - `NEODB_SEARCH_SITES`
 - `NEODB_FANOUT_LIMIT_DAYS`
 - `TAKAHE_REMOTE_PRUNE_HORIZON`
 - `NEODB_HIDDEN_CATEGORIES`

### External item sources
 - `SPOTIFY_API_KEY`
 - `TMDB_API_V3_KEY`
 - `GOOGLE_API_KEY`
 - `DISCOGS_API_KEY`
 - `IGDB_API_CLIENT_ID`, `IGDB_API_CLIENT_SECRET`
 - `BGG_API_TOKEN`
 - `MAL_API_CLIENT_ID` - client id of an app registered at https://myanimelist.net/apiconfig, required for MyAnimeList
 - `STEAM_API_KEY`

### Scraping providers
 - `NEODB_DOWNLOADER_PROVIDERS`
 - `NEODB_DOWNLOADER_SCRAPFLY_KEY`
 - `NEODB_DOWNLOADER_DECODO_TOKEN`
 - `NEODB_DOWNLOADER_SCRAPERAPI_KEY`
 - `NEODB_DOWNLOADER_SCRAPINGBEE_KEY`
 - `NEODB_DOWNLOADER_CUSTOMSCRAPER_URL`
 - `NEODB_DOWNLOADER_PROXY_LIST`
 - `NEODB_DOWNLOADER_BACKUP_PROXY`
 - `NEODB_DOWNLOADER_REQUEST_TIMEOUT`
 - `NEODB_DOWNLOADER_CACHE_TIMEOUT`
 - `NEODB_DOWNLOADER_RETRIES`

### Translation
 - `DEEPL_API_KEY`
 - `LT_API_URL`, `LT_API_KEY`

### Administration
 - `DISCORD_WEBHOOKS`
 - `THREADS_APP_ID`, `THREADS_APP_SECRET`
 - `NEODB_MASTODON_CLIENT_SCOPE`
 - `NEODB_LOGIN_MASTODON_TIMEOUT`
 - `NEODB_DISABLE_CRON_JOBS`
 - `INDEX_ALIASES`
 - `SKIP_MIGRATIONS` - skipped post-migration job keys. Configure these in Admin > Advanced > "Skip Migration Jobs". The UI value is read by the worker at dequeue time without a restart.
