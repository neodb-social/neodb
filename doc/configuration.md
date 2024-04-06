Configuration
=============


Settings you may want to change
-------------------------------
most settings resides in `settings.py`, a few notable ones:

 - `SECRET_KEY` must use your own, back it up well somewhere
 - `SITE_INFO` change by you need
 - `MASTODON_ALLOW_ANY_SITE` set to `True` so that user can login via any Mastodon API compatible sites (e.g. Mastodon/Pleroma)
 - `MASTODON_CLIENT_SCOPE` change it later may invalidate app token granted previously
 - `ADMIN_URL` admin page url, keep it private
 - `SEARCH_BACKEND` should be ~~either~~ `TYPESENSE` ~~or `MEILISEARCH`~~ so that search and index can function. `None` will use default database search, which is for development only and may gets deprecated soon.
   - `MEILISEARCH` support is removed due to lack of usage, feel free to PR if you want to


Settings for Scrapers
---------------------

TBA


Other maintenance tasks
-----------------------

Add alias to your shell for easier access

```
alias neodb-manage='docker-compose --profile production run shell neodb-manage'
```

Enable Developer Console

```
neodb-manage createapplication --client-id NEODB_DEVELOPER_CONSOLE --skip-authorization --name 'NeoDB Developer Console' --redirect-uris 'https://example.org/lol'  confidential authorization-code
```
