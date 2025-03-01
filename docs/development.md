Development
===========

Overview
--------
NeoDB is a Django project, and it runs side by side with a [modified version](https://github.com/neodb-social/neodb-takahe) of [Takahē](https://github.com/jointakahe/takahe) (a separate Django project, code in `neodb_takahe` folder of this repo as submodule). They communicate with each other mainly thru database and task queue, [the diagram](troubleshooting.md#containers) demonstrates a typical architecture. Currently the two are loosely coupled, so you may take either one offline without immediate impact on the other, which makes it very easy to conduct maintenance and troubleshooting separately. In the future, they may get combined but it's not decided and will not be decided very soon.


Prerequisite
------------
- Python 3.12.x
- Docker Compose v2 or newer


Prepare the code
----------------
When checking out NeoDB source code, make sure submodules are also checked out:
```
git clone https://github.com/neodb-social/neodb.git
cd neodb
git submodule update --init
```

Install [rye](http://rye.astral.sh) package manager, packages and pre-commit hooks:
```
curl -sSf https://rye.astral.sh/get | bash
rye sync
. .venv/bin/activate
pre-commit install
```

To develop Takahe, install requirements(-dev) and pre-commit hooks for `neodb-takahe` project as well, preferably using a different virtual environment.

Note: the virtual environments and packages installed in this step are mostly for linting, the actual virtual environments and packages are from NeoDB docker image, and they can be configured differently, more on this later in this document.


Start local instance for development
------------------------------------
Follow [install guide](install.md) to create `.env` in the root folder of NeoDB code, including at least these configuration:
```
NEODB_SITE_NAME="My Test"
NEODB_SITE_DOMAIN=mydomain.dev
NEODB_SECRET_KEY=_random_string__50_characters_of_length__no_whitespaces_
NEODB_IMAGE=neodb/neodb:edge
NEODB_DEBUG=True
```

Download docker images and start pgsql/redis/typesense before initializing database schema:
```
docker compose --profile dev pull
docker compose up -d
```

Initialize database schema:
```
docker compose --profile dev run --rm dev-shell takahe-manage collectstatic --noinput
docker compose --profile dev run --rm dev-shell neodb-init
```

Start the cluster:
```
docker compose --profile dev up -d
```

Watch the logs:
```
docker compose --profile dev logs -f
```

Now the local development instance is ready to serve at `http://localhost:8000`, but to develop or test anything related with ActivityPub, reverse proxying it from externally reachable https://`NEODB_SITE_DOMAIN`/ is required; https is optional theoretically but in reality required for various compatibility reasons.

Note: `dev` profile is for development only, and quite different from `production`, so always use `--profile dev` instead of `--profile production`, more on those differences later in this document.


Common development tasks
------------------------
Shutdown the cluster:
```
docker compose --profile dev down
```

Restart background tasks (unlike web servers, background tasks don't auto reload after code change):
```
docker-compose --profile dev restart dev-neodb-worker dev-takahe-stator
```

When updating code, always update submodules:
```
git pull
git submodule update --init
```
With newer git 2.15+, you main use `git pull --recurse-submodules` or `git config --global submodule.recurse true` to make it automatic.


To save some typing, consider adding some aliases to `~/.profile`:
```
alias neodb-logs='docker compose --profile dev logs'
alias neodb-shell='docker compose --profile dev run --rm dev-shell'
alias neodb-manage='docker compose --profile dev run --rm dev-shell neodb-manage'
```

Always use `neodb-init`, not `python3 manage.py migrate`, to update db schema after updating code:
```
neodb-shell neodb-init
```

Run unit test:
```
neodb-manage test
```

Update translations:
```
django-admin makemessages --no-wrap --no-obsolete -i .venv -i neodb-takahe --keep-pot -l zh_Hans -l zh_Hant

# edit .po files, run the following to make sure edits are correct

django-admin makemessages --no-wrap --no-obsolete -i .venv -i neodb-takahe --keep-pot -l zh_Hans -l zh_Hant
django-admin compilemessages -i .venv -i neodb-takahe
```

Preview documentation:
```
python -m mkdocs serve
```

Development in Docker Compose
-----------------------------
The `dev` profile is different from `production`:

- code in `NEODB_SRC` (default: .) and `TAKAHE_SRC` (default: ./neodb-takahe) will be mounted and used in the container instead of code in the image
- `runserver` with autoreload will be used instead of `gunicorn` for both neodb and takahe web server
- /static/ and /s/ url are not map to pre-generated/collected static file path,  `NEODB_DEBUG=True` is required locate static files from source code
- one `rqworker` container will be started, instead of two
- use `dev-shell` and `dev-root` to invoke shells, instead of `shell` and `root`
- there's no automatic `migration` container, but it can be triggered manually via `docker compose run dev-shell neodb-init`

Note:

- Python virtual environments inside docker image, which are `/neodb-venv` and `/takahe-venv`, will be used by default. They can be changed to different locations with `TAKAHE_VENV` and `NEODB_VENV` if needed, usually in a case of development code using a package not in docker venv.
- Some packages inside python virtual environments are platform dependent, so mount venv built by macOS host into the Linux container will likely not work.
- Python servers are launched as `app` user, who has no write access to anywhere except /tmp and media path, that's by design.
- Database/redis/typesense used in the container cluster are not accessible from host directly, which is by design. Querying them can be done by one of the following:
    - `neodb-manage dbshell`
    - `neodb-shell redis-cli -h redis`
    - or create `compose.override.yml` to uncomment `ports` section.
- To expose the neodb and takahe web server directly, in the folder for configuration, create `compose.override.yml` with the following content:

```
services:
  dev-neodb-web:
    ports:
      - "8001:8000"

  dev-takahe-web:
    ports:
      - "8002:8000"
```


Development with Github Codespace
---------------------------------
At the time of writing, docker compose will work in Github Codespace by adding this in `.env`:

```
NEODB_SITE_DOMAIN=${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}
```


Applications
------------
Main Django apps for NeoDB:

 - `users` manages user in typical Django fashion
 - `mastodon` this leverages [Mastodon API](https://docs.joinmastodon.org/client/intro/), [Threads API](https://developers.facebook.com/docs/threads/) and [ATProto}(https://atproto.com) for user login and data sync
 - `catalog` manages different types of items user may collect, and scrapers to fetch from external resources, see [catalog.md](internals/catalog.md) for more details
 - `journal` manages user created content(review/ratings) and lists(collection/shelf/tag/note), see [journal.md](internals/journal.md) for more details
 - `social` present timeline and notification for local users
 - `takahe` communicate with Takahe (a separate Django server, run side by side with this server, code in `neodb_takahe` as submodule), see [federation.md](internals/federation.md) for customization of ActivityPub protocol
 - `legacy` this is only used by instances upgraded from 0.4.x and earlier, to provide a link mapping from old urls to new ones. If your journey starts with 0.5 and later, feel free to ignore it.


Miscellaneous notes
-------------------
If models in `takahe/models.py` are changed, instead of adding incremental migrations, just regenerate `takahe/migrations/0001_initial.py` instead, because these migrations will never be applied except for constructing a test database.

A `libsass` wheel is stored in the repo to speed up docker image building process in Github Action.
