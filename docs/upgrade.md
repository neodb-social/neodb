Upgrade
=======

Check the [release notes](https://github.com/neodb-social/neodb/releases), update `compose.yml` and `.env` as instructed.

If there is `compose.override.yml`, make sure it's compatible with the updated `compose.yml`.

Pull the latest container images:
```bash
docker compose --profile production pull
```

Restart the entire cluster:
```bash
docker compose --profile production up -d
```

Optionally, clean up old images:
```bash
docker system prune -af
```

Upgrade PostgreSQL
------------------

The cluster runs two PostgreSQL servers, `neodb-db` and `takahe-db`. They use the version in `POSTGRES_VERSION` in `.env`. If `.env` does not set it, `compose.yml` uses 14. PostgreSQL 14 reached end of life on 2026-11-12. New installs from `neodb.env.example` use 17.

A new major version cannot read the data files of an older one. If you only change `POSTGRES_VERSION`, the databases do not start. Move the data with a dump and a restore. The site is offline during these steps.

Set the data path from `.env` in your shell:
```bash
NEODB_DATA=/var/lib/neodb
```

Stop the cluster, then start only the two databases:
```bash
docker compose --profile production down
docker compose up -d --wait neodb-db takahe-db
```

Dump both databases:
```bash
docker compose exec -T neodb-db pg_dump -U neodb -Fc neodb > neodb.dump
docker compose exec -T takahe-db pg_dump -U takahe -Fc takahe > takahe.dump
```

Stop the databases and move the old data aside:
```bash
docker compose down
mv $NEODB_DATA/neodb-db $NEODB_DATA/neodb-db.pg14
mv $NEODB_DATA/takahe-db $NEODB_DATA/takahe-db.pg14
```

Set `POSTGRES_VERSION=17-alpine` in `.env`. Start the new empty databases and restore the dumps:
```bash
docker compose up -d --wait neodb-db takahe-db
docker compose exec -T neodb-db pg_restore -U neodb -d neodb < neodb.dump
docker compose exec -T takahe-db pg_restore -U takahe -d takahe < takahe.dump
```

Start the cluster:
```bash
docker compose --profile production up -d
```

Keep the `.pg14` folders and the dump files until you confirm that the site works.

Do not use PostgreSQL 18 or later with this `compose.yml`. From 18, the official image keeps its data in a different path inside the container, and it does not start with the volume that `compose.yml` mounts.
