# NeoDB

![](https://github.com/neodb-social/neodb/actions/workflows/check.yml/badge.svg?branch=main)
![](https://github.com/neodb-social/neodb/actions/workflows/tests.yml/badge.svg?branch=main)
![](https://github.com/neodb-social/neodb/actions/workflows/publish.yml/badge.svg?branch=main)
![](https://github.com/neodb-social/neodb/actions/workflows/publish-tags.yml/badge.svg)

NeoDB (fka boofilsic) is an open source project and free service to help users manage, share and discover collections, reviews and ratings for culture products (e.g. books, movies, music, podcasts, games and performances) in Fediverse.

[NeoDB.social](https://neodb.social) and [NiceDB](https://nicedb.org) are free instances hosted by volunteers. Your support is essential to keep the service free and open-sourced.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/neodb)

## Features
- Manage a shared catalog of books/movies/tv shows/music album/games/podcasts/performances
  + search or create catalog items in each category
  + one click create item with links to 3rd party sites:
    * Goodreads
    * IMDB
    * The Movie Database
    * Douban
    * Google Books
    * Discogs
    * Spotify
    * Apple Music
    * Bandcamp
    * Steam
    * IGDB
    * Bangumi
    * any RSS link to a podcast
- Logged in users can manage their collections:
  + mark an item as wishlist/in progress/complete
  + rate and write reviews for an item
  + create tags for an item, either privately or publicly
  + create and share list of items
  + tracking progress of a list (e.g. personal reading challenges)
  + Import and export full user data archive
  + import list or archives from some 3rd party sites:
    * Goodreads reading list
    * Douban archive (via Doufen)
- Social features:
  + view home feed with friends' activities
    * every activity can be set as viewable to self/follower-only/public
    * eligible items, e.g. podcasts and albums, are playable in feed
  + login with other Fediverse server account and import social graph
    * supported servers: Mastodon/Pleroma/Firefish/GoToSocial/Pixelfed/friendica/Takahē
  + share collections and reviews to Fediverse ~~and Twitter~~ feed
  + ActivityPub support is under active development
    * NeoDB users can interact with users on other ActivityPub services like Mastodon and Pleroma
    * NeoDB instances communicate via an extended version of ActivityPub
    * NeoDB instances may share public rating and reviews with relays
    * implementation is based on [Takahē](https://jointakahe.org/) server
- Other
  + i18n/language support are planned

## Install
Please see [doc/install.md](doc/install.md)

## Bug Report
 - to file a bug for NiceDB, please create an issue [here](https://github.com/doubaniux/boofilsic/issues/new)
 - to file a bug or request new features for NeoDB, please contact NeoDB on [Fediverse](https://mastodon.social/@neodb) or [Twitter](https://twitter.com/NeoDBsocial)

## Contribution
 - To build application with NeoDB API, documentation is available in [NeoDB API Developer Console](https://neodb.social/developer/)
 - To help develop NeoDB, please see [doc/development.md](doc/development.md) for some basics to start with
 - Join our Discord community to share your ideas/questions/creations, links available on [our Fediverse profile](https://mastodon.social/@neodb)

## Sponsor
If you like this project, please consider sponsoring
 - NeoDB on [ko-fi](https://ko-fi.com/neodb) or [liberapay](https://liberapay.com/neodb)
 - NiceDB on [Patreon](https://patreon.com/tertius).
