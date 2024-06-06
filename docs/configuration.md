# Configuration


## Settings you may want to change

absolutely set these before start the instance for the first time:

 - NEODB_SECRET_KEY - 50 characters of random string, no white space
 - NEODB_SITE_NAME - the name of your site
 - NEODB_SITE_DOMAIN - the domain name of your site

`NEODB_SECRET_KEY` and `NEODB_SITE_DOMAIN` must not be changed later.

if you are doing debug or development:

 - NEODB_DEBUG - True will turn on debug for both neodb and takahe, turn off relay, and reveal self as debug mode in nodeinfo (so peers won't try to run fedi search on you)
 - NEODB_IMAGE - the docker image to use, `neodb/neodb:edge` for the main branch


## Settings for Scrapers

TBA


## Other maintenance tasks

Add alias to your shell for easier access

```
alias neodb-manage='docker-compose --profile production run shell neodb-manage'
```

Enable Developer Console

```
neodb-manage createapplication --client-id NEODB_DEVELOPER_CONSOLE --skip-authorization --name 'NeoDB Developer Console' --redirect-uris 'https://example.org/lol'  confidential authorization-code
```


## Multiple instances on one server

It's possible to run multiple clusters in one host server, as long as `NEODB_SITE_DOMAIN`, `NEODB_PORT` and `NEODB_DATA` are different.


## Scaling up

For high-traffic instance, spin up these configurations to a higher number, as long as the host server can handle them:

 - `NEODB_WEB_WORKER_NUM`
 - `NEODB_API_WORKER_NUM`
 - `NEODB_RQ_WORKER_NUM`
 - `TAKAHE_WEB_WORKER_NUM`
 - `TAKAHE_STATOR_CONCURRENCY`
 - `TAKAHE_STATOR_CONCURRENCY_PER_MODEL`

Further scaling up with multiple nodes (e.g. via Kubernetes) is beyond the scope of this document, but consider run db/redis/typesense separately, and then duplicate web/worker/stator containers as long as connections and mounts are properly configured; `migration` only runs once when start or upgrade, it should be kept that way.
