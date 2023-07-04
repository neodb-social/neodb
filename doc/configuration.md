Configuration
=============


Settings you may want to change
-------------------------------
most settings resides in `settings.py`, a few notable ones:

 - `SECRET_KEY` back it up well somewhere
 - `SITE_INFO['site_name']` change by you need
 - `CLIENT_NAME` site name shown in Mastodon app page
 - `APP_WEBSITE` external root url for your side
 - `REDIRECT_URIS` this should be `APP_WEBSITE + "/account/login/oauth"` . It can be multiple urls separated by `\n` , but not all Fediverse software support it well. Also note changing this later may invalidate app token granted previously
 - `MASTODON_ALLOW_ANY_SITE` set to `True` so that user can login via any Mastodon API compatible sites (e.g. Mastodon/Pleroma)
 - `MASTODON_CLIENT_SCOPE` change it later may invalidate app token granted previously
 - `ADMIN_URL` admin page url, keep it private
 - `SEARCH_BACKEND` should be either `TYPESENSE` or `MEILISEARCH` so that search and index can function. `None` will use default database search, which is for development only and may gets deprecated soon.


Settings for Scrapers
---------------------

TBA
