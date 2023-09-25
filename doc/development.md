Development
===========

Overview
--------
NeoDB is a Django project, and it runs side by side with a modified version of [Takahe](https://github.com/jointakahe/takahe) (a separate Django project, code in `neodb_takahe` as submodule). They communicate mainly thru database and task queue, the diagram in [Docker Installation](install-docker.md) demostrate a typical architecture. Currently the two are loosely coupled, so you may take either one offline without immediate impact on the other, which makes it very easy to conduct maintenance and troubleshooting separately. In the future, they may get combined but it's not decided and will not be decided very soon.

Before writing code
-------------------

Obviously a working version of local NeoDB instance has to be established first, see [install guide](install.md). If you are to test anything related with ActivityPub, a setup with externally reachable real domain name might be required, using `localhost` may cause quite a few issues here and there.

Install development related packages
```
python3 -m pip install -r requirements-dev.txt
```

Install pre-commit hooks
```
$ pre-commit install
pre-commit installed at .git/hooks/pre-commit
```

Make sure code in submodule is updated
```
git submodule update --init
```

then install requirements and pre-commit hook for `neodb-takahe` project (using a different venv is preferred)


Migration
---------
Always run `python3 manage.py migrate` for takahe first, before do that for neodb.

If models in `takahe/models.py` are changed, just regenerate `takahe/migrations/0001_initial.py` instead of adding incremental migrations, because these migrations will never be applied except for constructing a test database.


Run Test
--------
`python3 manage.py test` will run the tests

Alternative you may create the test database from freshly created database:
```
CREATE DATABASE test_neodb WITH TEMPLATE neodb;
```
and run the test without re-create it every time
```
$ python3 manage.py test --keepdb

Using existing test database for alias 'default'...
System check identified no issues (2 silenced).
........................................................
----------------------------------------------------------------------
Ran 56 tests in 1.100s

OK
Preserving test database for alias 'default'...
```

Development in Docker
---------------------
To run local source code with `docker compose`, add `NEODB_DEBUG=True` in `.env`, and use `--profile dev` instead of `--profile production` in commands. The `dev` profile is different from `production`:

- code in `NEODB_SRC` (default: .) and `TAKAHE_SRC` (default: ./neodb-takahe) will be mounted and used in the container instead of code in the image
- `runserver` with autoreload will be used instead of `gunicorn` for both neodb and takahe web server
- /static/ and /s/ url are not map to pre-generated/collected static file path,  `NEODB_DEBUG=True` will locate static files from source code
- one `rqworker` container will be started, instead of two
- use `dev-shell` and `dev-root` to invoke shells, instead of `shell` and `root`
- there's no automatic `migration` container, but it can be triggered manually via `docker compose run dev-shell neodb-init`

Note:
- Python virtual environments inside docker image, which are `/neodb-venv` and `/takahe-venv`, will be used by default. They can be changed to different locations with `TAKAHE_VENV` and `NEODB_VENV` if needed, usually in a case of development code using a package not in docker venv.
- Some packages inside python virtual environments are platform dependent, so mount venv built by macOS host into the Linux container will likely not work.
- Python servers are launched as `app` user, who has no write access to anywhere except /tmp and media path, that's by design.
- Database/redis used in the container cluster are not accessible outside, which is by design. Querying them can be done by either apt update/install client packages in `dev-root` or `root` container, or a modified `docker-compose.yml` with `ports` section uncommented.

To run local unit tests, use `docker compose run dev-shell neodb-manage test`

Applications
------------
Main django apps for NeoDB:
 - `users` manages user in typical django fashion
 - `mastodon` this leverages [Mastodon API](https://docs.joinmastodon.org/client/intro/) and [Twitter API](https://developer.twitter.com/en/docs/twitter-api) for user login and data sync
 - `catalog` manages different types of items user may review, and scrapers to fetch from external resources, see [catalog.md](catalog.md) for more details
 - `journal` manages user created content(review/ratings) and lists(collection/shelf/tag), see [journal.md](journal.md) for more details
 - `social` present timeline for local users, see [social.md](social.md) for more details
 - `takahe` communicate with Takahe (a separate Django server, run side by side with this server, code in `neodb_takahe` as submodule)
 - `legacy` this is only used by instances upgraded from 0.4.x and earlier, to provide a link mapping from old urls to new ones. If your journey starts with 0.5 and later, feel free to ignore it.
