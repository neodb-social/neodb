![Tests](https://img.shields.io/github/actions/workflow/status/neodb-social/neodb/tests.yml?style=for-the-badge&color=56AA54&label=tests)
[![Translation](https://img.shields.io/weblate/progress/neodb?style=for-the-badge)](https://hosted.weblate.org/projects/neodb/neodb/)
[![GitHub Release](https://img.shields.io/github/v/release/neodb-social/neodb?style=for-the-badge&color=3791E0&logoColor=fff)](https://github.com/neodb-social/neodb/releases)
[![Docker Pulls](https://img.shields.io/docker/pulls/neodb/neodb?label=docker&color=3791E0&style=for-the-badge)](https://hub.docker.com/r/neodb/neodb)
[![GitHub License](https://img.shields.io/github/license/neodb-social/neodb?color=E69A48&style=for-the-badge)](https://github.com/neodb-social/neodb/blob/main/LICENSE)

# 🧩 NeoDB
_mark the things you love._

[NeoDB](https://neodb.net) (fka boofilsic) is an open source project and free service to help users manage, share and discover collections, reviews and ratings for culture products (e.g. books, movies, music, podcasts, games and performances) in Fediverse.

[NeoDB.social](https://neodb.social) and [NiceDB](https://nicedb.org) are free instances hosted by volunteers. Your support is essential to keep these services free and open-sourced.

Follow us on [Fediverse](https://mastodon.online/@neodb), [Bluesky](https://bsky.app/profile/neodb.net) or join our [Discord community](https://discord.gg/QBHkrV8bxK) to share your ideas/questions/creations

[![Mastodon](https://img.shields.io/mastodon/follow/106919732872456302?style=for-the-badge&logo=mastodon&logoColor=fff&label=%40neodb%40mastodon.social&color=6D75D2)](https://mastodon.social/@neodb)
[![Discord](https://img.shields.io/discord/1041738638364528710?label=Discord&logo=discord&logoColor=fff&color=6D75D2&style=for-the-badge)](https://discord.gg/QBHkrV8bxK)
[![Kofi](https://img.shields.io/badge/Ko--Fi-Donate-orange?label=Support%20NeoDB%20on%20Ko-fi&style=for-the-badge&color=ff5f5f&logo=ko-fi)](https://ko-fi.com/neodb)

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
    * Board Game Geek
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
    * Letterboxd watch list
    * Douban archive (via [Doufen](https://doufen.org/))
- Social features:
  + view home feed with friends' activities
    * every activity can be set as viewable to self/follower-only/public
    * eligible items, e.g. podcasts and albums, are playable in feed
  + login with other Fediverse identity and import social graph
    * supported servers: Mastodon/Pleroma/Firefish/GoToSocial/Pixelfed/friendica/Takahē
  + login with Bluesky / ATProto identity and import social graph
  + login with threads.net (requires app verification by Meta)
  + share collections and reviews to Fediverse/Bluesky/Threads
- ActivityPub support
  + NeoDB users can follow and interact with users on other ActivityPub services like Mastodon and Pleroma
  + NeoDB instances communicate with each other via an extended version of ActivityPub
  + NeoDB instances may share public rating and reviews with a default relay
  + implementation is based on [Takahē](https://jointakahe.org/) server
- ATProto support
  + NeoDB is not a PDS, but may publish posts to user feed
- Other
  + i18n: English, Danish and Simp/Trad Chinese available; contribution for more languages welcomed

## Host your own instance
Please see [docs/install.md](docs/install.md)

## Contribution
 - To build application with NeoDB API, documentation is available in [NeoDB API Developer Console](https://neodb.social/developer/)
 - To help develop NeoDB, please see [docs/development.md](docs/development.md) for some basics to start with
 - To translate NeoDB to more languages, please join [our project on Weblate](https://hosted.weblate.org/projects/neodb/neodb/)

## Sponsor
If you like this project, please consider sponsoring
 - NeoDB on [ko-fi](https://ko-fi.com/neodb)
 - NiceDB on [Patreon](https://patreon.com/tertius).
