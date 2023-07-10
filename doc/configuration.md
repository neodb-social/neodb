Configuration
=============


Settings you may want to change
-------------------------------
most settings resides in `settings.py`, a few notable ones:

 - `SECRET_KEY` must use your own, back it up well somewhere
 - `SITE_INFO` change by you need
 - `REDIRECT_URIS` this should be `SITE_INFO["site_url"] + "/account/login/oauth"` . It used to be multiple urls separated by `\n` , but now it must be only one url, bc not all Fediverse software support >1 urls very well. Also note changing this later may invalidate app token granted previously
 - `MASTODON_ALLOW_ANY_SITE` set to `True` so that user can login via any Mastodon API compatible sites (e.g. Mastodon/Pleroma)
 - `MASTODON_CLIENT_SCOPE` change it later may invalidate app token granted previously
 - `ADMIN_URL` admin page url, keep it private
 - `SEARCH_BACKEND` should be ~~either~~ `TYPESENSE` ~~or `MEILISEARCH`~~ so that search and index can function. `None` will use default database search, which is for development only and may gets deprecated soon.
   - `MEILISEARCH` support is removed due to lack of usage, feel free to PR if you want to


Settings for Scrapers
---------------------

TBA
