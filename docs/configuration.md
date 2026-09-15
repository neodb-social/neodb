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


## S3 and Compatible Storage

To test storage configuration, you can use the following command to upload a test file and check if it's accessible:

```
neodb-manage catalog storage-test
```

### Minio

If you are using Minio or [its forks](https://github.com/minio/minio/network) for local S3-compatible storage, add the following configuration to `compose.override.yml` (change `minio/minio` to your chosen fork as the original one is unmaintained and may have known security issues):

```
services:
  minio:
    image: minio/minio:latest
    command: server --console-address :9001
    environment:
      MINIO_DOMAIN: ${MINIO_DOMAIN}
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: change_password
      MINIO_VOLUMES: /var/lib/minio
    volumes:
      - ${NEODB_DATA:-../data}/minio-files:/var/lib/minio
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
    ports:
      - 9000:9000
      - 9001:9001
```

And add these settings to `.env`:
```
MINIO_DOMAIN=my.media.domain
MEDIA_BACKEND=s3-insecure://minioadmin:change_password@minio:9000/media
MEDIA_URL=https://my.media.domain/media/
```

Also make sure `my.media.domain` maps to your Minio server (port 9000 as configured above).


### Garage

[Garage](https://garagehq.deuxfleurs.fr/) is a lightweight S3-compatible storage engine. Version 2.3.0 and later can configure a single-node cluster and its first bucket at start, thus the commands below are much fewer than the [Garage quick start](https://garagehq.deuxfleurs.fr/documentation/quick-start/) gives for a cluster.

Create a `garage.toml` configuration file. Make a new `rpc_secret` with `openssl rand -hex 32`:
```
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
replication_factor = 1
rpc_bind_addr = "[::]:3901"
rpc_secret = "YOUR_RPC_SECRET"

[s3_api]
s3_region = "garage"
api_bind_addr = "[::]:3900"

[s3_web]
bind_addr = "[::]:3902"
root_domain = ".my.media.domain"
```

Add the following to `compose.override.yml`. Make the access key with `echo GK$(openssl rand -hex 16)` and the secret key with `openssl rand -hex 32`:
```
services:
  garage:
    image: dxflrs/garage:v2.4.1
    command: /garage server --single-node --default-bucket
    environment:
      GARAGE_DEFAULT_ACCESS_KEY: YOUR_ACCESS_KEY
      GARAGE_DEFAULT_SECRET_KEY: YOUR_SECRET_KEY
      GARAGE_DEFAULT_BUCKET: media
    volumes:
      - ${NEODB_DATA:-../data}/garage/garage.toml:/etc/garage.toml
      - ${NEODB_DATA:-../data}/garage/data:/var/lib/garage/data
      - ${NEODB_DATA:-../data}/garage/meta:/var/lib/garage/meta
    ports:
      - 3900:3900
      - 3902:3902
```

`--single-node` makes the cluster layout, and `--default-bucket` makes the key and the `media` bucket. Neither of them makes the bucket public, thus give the bucket public read access after the first start:
```
docker compose exec garage /garage -c /etc/garage.toml bucket website --allow media
```

Add these settings to `.env`, using the same key ID and secret key:
```
MEDIA_BACKEND=s3-insecure://YOUR_ACCESS_KEY:YOUR_SECRET_KEY@garage:3900/media
MEDIA_URL=https://media.my.media.domain/
```

Garage serves files publicly via its S3 Web endpoint (port 3902) using virtual-host-style routing. The `MEDIA_URL` hostname must match `{bucket}.{root_domain}` configured in the `[s3_web]` section of `garage.toml`. For example, with `root_domain = ".my.media.domain"` and bucket `media`, the public URL becomes `https://media.my.media.domain/`. Make sure DNS for that hostname points to Garage's port 3902.


### SeaweedFS

[SeaweedFS](https://github.com/seaweedfs/seaweedfs) is a distributed storage system with S3 API support. Add the following to `compose.override.yml`, mounting an [S3 credentials config](https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API) file with anonymous `Read` and an admin identity (see [Docker Compose for S3](https://github.com/seaweedfs/seaweedfs/wiki/Docker-Compose-for-S3) for details):

```
services:
  seaweedfs:
    image: chrislusf/seaweedfs
    command: "server -s3 -s3.config /etc/seaweedfs/config.json"
    volumes:
      - ${NEODB_DATA:-../data}/seaweedfs/config.json:/etc/seaweedfs/config.json
      - ${NEODB_DATA:-../data}/seaweedfs/data:/data
    ports:
      - 8333:8333
```

Create the `media` bucket after first start (using [awscli](https://aws.amazon.com/cli/) or any S3 client):
```
aws --endpoint-url http://localhost:8333 s3 mb s3://media
```

Add these settings to `.env`, matching the credentials in the config file:
```
MEDIA_BACKEND=s3-insecure://some_access_key:some_secret_key@seaweedfs:8333/media
MEDIA_URL=https://my.media.domain/media/
```

Make sure `my.media.domain` maps to your SeaweedFS server (port 8333). Files are publicly readable via the same port thanks to the anonymous read identity.


### VersityGW

[VersityGW](https://github.com/versity/versitygw) is an S3 gateway that keeps each object as a plain file in a local directory. Add the following to `compose.override.yml`:

```
services:
  versitygw:
    image: ghcr.io/versity/versitygw:v1.8.0
    environment:
      ROOT_ACCESS_KEY: neodbadmin
      ROOT_SECRET_KEY: change_password
      VGW_BACKEND: posix
      VGW_BACKEND_ARGS: /data/s3
      VGW_IAM_DIR: /data/iam
    volumes:
      - ${NEODB_DATA:-../data}/versitygw/s3:/data/s3
      - ${NEODB_DATA:-../data}/versitygw/iam:/data/iam
    ports:
      - 7070:7070
```

Put the `s3` directory on a filesystem that supports extended attributes. VersityGW keeps the content type, the ETag and the bucket policy in extended attributes. If your filesystem does not support them, add `--sidecar /data/meta` to `VGW_BACKEND_ARGS` and mount a second directory at `/data/meta`, where VersityGW will keep the same data as plain files. Select one of the two modes before you create the bucket, because VersityGW does not read the metadata of the other mode.

Do not move an existing media folder into the bucket directory. VersityGW gives the files no content type, thus it sends them as `text/plain` and browsers will not show the images. Copy the folder in through the S3 API instead, which sets the content type from the file extension:
```
aws --endpoint-url http://localhost:7070 s3 sync /path/to/neodb-media s3://media/
```

If the folder is too large to copy twice, you can put it in the bucket directory and then give each object a content type with a server-side copy, which does not send the data again:
```
aws --endpoint-url http://localhost:7070 s3api copy-object --bucket media \
  --key covers/example.jpg --copy-source media/covers/example.jpg \
  --metadata-directive REPLACE --content-type image/jpeg
```
Such objects still have no ETag. Add `--default-etag <value>` to `VGW_BACKEND_ARGS` if your clients need one. VersityGW has no command to build the metadata of existing files ([feature request](https://github.com/versity/versitygw/issues/2304)).

Create the `media` bucket after first start, then let anonymous users read it. VersityGW does not allow bucket ACLs by default, so you must add a bucket policy (using [awscli](https://aws.amazon.com/cli/) or any S3 client):
```
export AWS_ACCESS_KEY_ID=neodbadmin
export AWS_SECRET_ACCESS_KEY=change_password
export AWS_DEFAULT_REGION=us-east-1
aws --endpoint-url http://localhost:7070 s3 mb s3://media
aws --endpoint-url http://localhost:7070 s3api put-bucket-policy --bucket media \
  --policy '{"Statement":[{"Effect":"Allow","Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::media/*"}]}'
```

Add these settings to `.env`:
```
MEDIA_BACKEND=s3-insecure://neodbadmin:change_password@versitygw:7070/media
MEDIA_URL=https://my.media.domain/media/
```

Make sure `my.media.domain` maps to your VersityGW server (port 7070 as configured above). NeoDB always builds media URLs with `https`, so serve that domain through a TLS reverse proxy in front of VersityGW.


### S2

[S2](https://github.com/mojatter/s2) is a small S3 server that can serve a directory of files it did not write itself. It finds the content type of each file from the file extension, so you can put an existing media folder into a bucket and use it immediately. S2 is a young project, and its authors give local development as the primary use.

Write a configuration file, for example `${NEODB_DATA:-../data}/s2/s2.json`. The `"*"` account is the anonymous reader, which makes the media files publicly readable:
```
{
  "listen": ":9000",
  "type": "osfs",
  "root": "/var/lib/s2",
  "user": "neodbadmin",
  "password": "change_password",
  "users": [
    {
      "access_key_id": "*",
      "policy": {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Sid": "PublicRead",
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::media/*"
          }
        ]
      }
    }
  ]
}
```

Add the following to `compose.override.yml`. Each directory below the root is a bucket, so the `media` bucket needs no separate creation step:
```
services:
  s2:
    image: mojatter/s2-server:0.17.0
    environment:
      S2_SERVER_CONFIG: /etc/s2/s2.json
      S2_SERVER_CONSOLE_LISTEN: ""
    volumes:
      - ${NEODB_DATA:-../data}/s2/s2.json:/etc/s2/s2.json
      - ${NEODB_DATA:-../data}/s2/data:/var/lib/s2
    ports:
      - 9000:9000
```

Add these settings to `.env`:
```
MEDIA_BACKEND=s3-insecure://neodbadmin:change_password@s2:9000/media
MEDIA_URL=https://my.media.domain/media/
```

Make sure `my.media.domain` maps to your S2 server (port 9000 as configured above). NeoDB always builds media URLs with `https`, so serve that domain through a TLS reverse proxy in front of S2.

To change from `MEDIA_BACKEND=local://`, move the contents of the `neodb-media` and `takahe-media` directories into `s2/data/media`. NeoDB and takahe use different key prefixes, thus their files do not conflict. S2 writes into the same directory, where it keeps a `.meta` directory of the metadata of each file it receives through the S3 API.

S2 gives the files it did not write a placeholder ETag. It also does not answer conditional requests, thus a browser gets the full file each time instead of a `304`. The example above turns the web console off; remove `S2_SERVER_CONSOLE_LISTEN` to get it on port 9001.


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
