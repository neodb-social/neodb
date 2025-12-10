# Troubleshooting


## Tips

 - `docker compose ps` to see if any service is down, (btw it's normal that `migration` is in `Exit 0` state)
 - `docker compose run shell` to run a shell into the cluster; or `docker compose run root` for root shell, and `apt` is available if extra package needed
 - see `Debug in Docker` in [development doc](development.md) for debugging tips


## Containers

a typical neodb cluster looks like:

```mermaid
flowchart TB
    web[[Your reverse proxy server with SSL]] --- neodb-nginx[nginx listening on localhost:8000]
    subgraph Containers managed by compose.yml
    neodb-nginx --- neodb-web
    neodb-nginx --- takahe-web
    neodb-worker --- typesense[(typesense)]
    neodb-worker --- neodb-db[(neodb-db)]
    neodb-worker --- redis[(redis)]
    neodb-web --- typesense
    neodb-web --- neodb-db
    neodb-web --- redis
    neodb-web ---  takahe-db[(takahe-db)]
    migration([migration]) --- neodb-db
    migration --- takahe-db
    takahe-web --- takahe-db
    takahe-web --- redis
    takahe-stator --- takahe-db
    takahe-stator --- redis
    end
```


## Data Folders

a typical neodb folder after starting up should look like:

```
mysite
├── data                # neodb data folder, location can be changed via NEODB_DATA in .env
│   ├── neodb-db        # neodb database
│   ├── neodb-media     # uid must be 1000 (app user in docker image), chmod if not so
│   ├── redis           # neodb/takahe cache
│   ├── takahe-cache    # uid must be 33 (www-data user in docker image), chmod if not so
│   ├── takahe-db       # neodb database
│   ├── takahe-media    # uid must be 1000 (app user in docker image), chmod if not so
│   ├── typesense       # neodb search index
│   └── www-root        # neodb web root for robots.txt, logo.png and etc
└── config
    ├── compose.yml     # copied from neodb release
    └── .env            # your configuration, see neodb.env.example
```
