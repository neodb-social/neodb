Upgrade
-------

Check the [release notes](https://github.com/neodb-social/neodb/releases), update `compose.yml` and `.env` as instructed.

If there is `compose.override.yml`, make sure it's compatible with the updated `compose.yml`.

Pull the latest container image
```bash
docker compose --profile production pull
```

Restart the entire cluster:
```bash
docker compose --profile production up -d
```

Optionally, clean up old images:
```bash
docker system prune -af --volumes
```
