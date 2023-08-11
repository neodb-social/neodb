Development
===========

Obviously a working version of local NeoDB instance has to be established first, see [install guide](install.md).

Install development related packages
```
pdm install --dev
```

Install pre-commit hooks
```
$ pdm run pre-commit install
pre-commit installed at .git/hooks/pre-commit
```

Manage Dependencies
-------------------

It's recommended to use PDM to add and remove dependencies.

Add a dependency:

```
pdm add some-package
```

Add a dev dependency:

```
pdm add -d some-package
```

Remove a dependency:

```
pdm remove some-package
```

Update all dependencies to the latest version in the lockfile:

```
pdm update
```

Refer to the [PDM documentation](https://pdm.fming.dev/latest/usage/dependency) for more details.

Run Test
--------
`pdm run manage.py test` will run the tests

Alternative you may create the test database from freshly created database:
```
CREATE DATABASE test_neodb WITH TEMPLATE neodb;
```
and run the test without re-create it every time
```
$ pdm run manage.py test --keepdb

Using existing test database for alias 'default'...
System check identified no issues (2 silenced).
........................................................
----------------------------------------------------------------------
Ran 56 tests in 1.100s

OK
Preserving test database for alias 'default'...
```


Applications
------------
Main django apps for NeoDB:
 - `users` manages user in typical django fashion
 - `mastodon` this leverages [Mastodon API](https://docs.joinmastodon.org/client/intro/) and [Twitter API](https://developer.twitter.com/en/docs/twitter-api) for user login and data sync
 - `catalog` manages different types of items user may review, and scrapers to fetch from external resources, see [catalog.md](catalog.md) for more details
 - `journal` manages user created content(review/ratings) and lists(collection/shelf/tag), see [journal.md](journal.md) for more details
 - `social` manages timeline for local users and ActivityStreams for remote servers, see [social.md](social.md) for more details
 - `legacy` this is only used by instances upgraded from 0.4.x and earlier, to provide a link mapping from old urls to new ones. If your journey starts with 0.5 and later, feel free to ignore it.
