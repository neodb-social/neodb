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

The cluster runs two PostgreSQL servers, `neodb-db` and `takahe-db`. They use the version in `POSTGRES_VERSION` in `.env`. If `.env` does not set it, `compose.yml` uses 14. PostgreSQL 14 reached end of life on 2026-11-12. New installs from `neodb.env.example` use 18.

Before you upgrade, make sure that your `compose.yml` sets `PGDATA=/var/lib/postgresql/data` for both databases, as the current one does. From 18, the official image keeps its data in a different path by default, and without this setting it does not start with the volume that `compose.yml` mounts.

A new major version cannot read the data files of an older one. If you only change `POSTGRES_VERSION`, the databases do not start. Move the data with a dump and a restore. The site is offline during these steps.

Set the data path from `.env` in your shell:
```bash
NEODB_DATA=/var/lib/neodb
```

Check that `$NEODB_DATA` has no `neodb-db.pg14` or `takahe-db.pg14` folder. If one exists, an earlier attempt stopped part way and that folder may hold the only copy of your data. Do not remove it: go back to the old data as described at the end of this section, then start again.

Stop the cluster, then start only the two databases:
```bash
docker compose --profile production down
docker compose up -d --wait neodb-db takahe-db
```

Dump both databases. The dump files hold all site data, so `umask 077` makes them readable only by you. If dump files from an earlier attempt exist, delete them first.
```bash
umask 077
docker compose exec -T neodb-db pg_dump -U neodb -Fc neodb > neodb.dump \
  && docker compose exec -T takahe-db pg_dump -U takahe -Fc takahe > takahe.dump \
  && echo "Dump OK"
```

Continue only if the output ends with `Dump OK`. If it does not, stop here. The old data is still in place, and `docker compose --profile production up -d` starts the site again.

Stop the databases and move the old data aside:
```bash
docker compose down \
  && mv $NEODB_DATA/neodb-db $NEODB_DATA/neodb-db.pg14 \
  && mv $NEODB_DATA/takahe-db $NEODB_DATA/takahe-db.pg14 \
  && echo "Move OK"
```

Continue only if the output ends with `Move OK`. If it does not, move back any folder that was already moved, for example `mv $NEODB_DATA/neodb-db.pg14 $NEODB_DATA/neodb-db`, and start the cluster as usual.

Set `POSTGRES_VERSION=18-alpine` in `.env`. Start the new empty databases and restore the dumps:
```bash
docker compose up -d --wait neodb-db takahe-db
docker compose exec -T neodb-db pg_restore -U neodb -d neodb < neodb.dump \
  && docker compose exec -T takahe-db pg_restore -U takahe -d takahe < takahe.dump \
  && echo "Restore OK"
```

Continue only if the output ends with `Restore OK`. If it does not, do not start the cluster. Go back to the old data:
```bash
docker compose down \
  && mv $NEODB_DATA/neodb-db $NEODB_DATA/neodb-db.failed \
  && mv $NEODB_DATA/takahe-db $NEODB_DATA/takahe-db.failed \
  && mv $NEODB_DATA/neodb-db.pg14 $NEODB_DATA/neodb-db \
  && mv $NEODB_DATA/takahe-db.pg14 $NEODB_DATA/takahe-db \
  && echo "Rollback OK"
```

If the output ends with `Rollback OK`, set `POSTGRES_VERSION` back to the value it had before the upgrade (`14-alpine` if it was not set) and start the cluster as usual. If it does not, fix the move that failed before you start the cluster.

Start the cluster:
```bash
docker compose --profile production up -d
```

Keep the `.pg14` folders and the dump files until you confirm that the site works. Then delete the dump files, because they hold all site data.
