Install
=======

For small and medium NeoDB instances, it's recommended to deploy as a container cluster with Docker Compose. To run a large instance, please see [scaling up](configuration.md#scaling-up) for some tips.

## Install docker compose

Follow [official instructions](https://docs.docker.com/compose/install/) to install Docker Compose if not yet.

Please verify its version is 2.x or higher before next step:

```
docker compose version
```

The rest of this doc assumes you can run docker commands without `sudo`, to verify that:

```
docker run --rm hello-world
```

Follow [official instructions](https://docs.docker.com/engine/install/linux-postinstall/) if it's not enabled, or use `sudo` to run commands in this doc.


## Prepare configuration files
 - create a folder for configuration, eg ~/mysite/config
 - grab `compose.yml` and `neodb.env.example` from [latest release](https://github.com/neodb-social/neodb/releases)
 - rename `neodb.env.example` to `.env`


## Set up .env file and web root
Change essential options like `NEODB_SITE_DOMAIN` in `.env` before starting the cluster for the first time. Changing them later may have unintended consequences, please make sure they are correct before exposing the service externally.

- `NEODB_SITE_NAME` - name of your site
- `NEODB_SITE_DOMAIN` - domain name of your site
- `NEODB_SECRET_KEY` - encryption key of session data
- `NEODB_DATA` is the path to store db/media/cache, it's `../data` by default, but can be any path that's writable
- `NEODB_DEBUG` - set to `False` for production deployment
- `NEODB_PREFERRED_LANGUAGES` - preferred languages when importing titles from 3rd party sites like TMDB and Steam, comma-separated list of ISO-639-1 two-letter codes, 'en,zh' by default.

Optionally, `robots.txt` and `logo.png` may be placed under `$NEODB_DATA/www-root/`.

See [neodb.env.example](https://raw.githubusercontent.com/neodb-social/neodb/main/neodb.env.example) and [configuration](configuration.md) for more options


## Start container

in the folder with `compose.yml` and `.env`, execute as the user you just created:
```
docker compose --profile production pull
docker compose --profile production up -d
```

Starting up for the first time might take a few minutes, depending on download speed, use the following commands for status and logs:
```
docker compose ps
docker compose --profile production logs -f
```

In a few seconds, the site should be up at 127.0.0.1:8000 , you may check it with:
```
curl http://localhost:8000/nodeinfo/2.0/
```

JSON response will be returned if the server is up and running:
```
{"version": "2.0", "software": {"name": "neodb", "version": "0.8-dev"}, "protocols": ["activitypub", "neodb"], "services": {"outbound": [], "inbound": []}, "usage": {"users": {"total": 1}, "localPosts": 0}, "openRegistrations": true, "metadata": {}}
```


## Make the site available publicly

Next step is to expose `http://127.0.0.1:8000` to external network as `https://yourdomain.tld` (NeoDB requires `https`). There are many ways to do it, you may use nginx or caddy as a reverse proxy server with an SSL cert configured, or configure a tunnel provider like cloudflared to do the same. Once done, you may check it with:

```
curl https://yourdomain.tld/nodeinfo/2.0/
```

You should see the same JSON response as above, and the site is now accessible to the public.


## Register an account and make it admin

If you have email sender properly configured, use this command to create an admin with a verified email (use any password as it won't be saved)

```
docker compose --profile production run --rm shell neodb-manage createsuperuser
```

Now open `https://yourdomain.tld` in your browser and register an account, assuming username `admin`

add the following line to `.env` to make it an admin account:

```
NEODB_ADMIN_USERS=admin
```

now restart the cluster to make it effective:

```bash
docker compose --profile production up -d
```

Now your instance should be ready to serve. More tweaks are available, see [configuration](configuration.md) for options.
