Configuration
=============


Settings you may want to change
-------------------------------
most settings resides in `settings.py`, a few notable ones:

absolutely set these before start the instance for the first time:

 - NEODB_SECRET_KEY - 50 characters of random string, no white space
 - NEODB_SITE_NAME - the name of your site
 - NEODB_SITE_DOMAIN - the domain name of your site

`NEODB_SECRET_KEY` and `NEODB_SITE_DOMAIN` must not be changed later.

if you are doing debug or development:

 - NEODB_DEBUG - True will turn on debug for both neodb and takahe, turn off relay, and reveal self as debug mode in nodeinfo (so peers won't try to run fedi search on you)
 - NEODB_IMAGE - the docker image to use, `neodb/neodb:edge` for the main branch


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
