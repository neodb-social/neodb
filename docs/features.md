# Features

NeoDB has various features, and you may imagine it as a mix of Mastodon, Goodreads, Letterboxd, RateYourMusic, Podchaser, and more.

## Public catalog

  - a shared catalog of books/movies/TV shows/music albums/games/podcasts/performances
  - search or create catalog items in each category
  - create an item in one click, with links to 3rd-party sites:
    - Goodreads
    - IMDB
    - The Movie Database
    - Douban
    - Google Books
    - Discogs
    - Spotify
    - Apple Music
    - Bandcamp
    - Steam
    - IGDB
    - Bangumi
    - Board Game Geek
    - any RSS link to a podcast
    - ...[full list](sites.md)


## Personal collections

  - mark an item as wishlist/in progress/complete/dropped
  - rate and write reviews for an item
  - write notes for an item with progress (e.g. reading notes at page 42)
  - create tags for an item, either privately or publicly
  - create and share list of items
  - track progress of a list (e.g. personal reading challenges)
  - Import and export full user data archive
  - import list or archives from some 3rd party sites (see [supported sites](sites.md) for details):
    - Goodreads reading list (CSV export)
    - StoryGraph reading list (CSV export)
    - Letterboxd watch list (ZIP export)
    - RateYourMusic album collection (CSV export)
    - Podcast subscriptions (OPML)
    - Douban archive (via [Doufen](https://doufen.org/))


## Social

  - publish and reply to posts in text and image
  - view home feed with friends' activities
    - every activity can be set as viewable to self/follower-only/public
    - eligible items, e.g. podcasts and albums, are playable in feed
  - login with other Fediverse identity and import social graph
    - supported servers: Mastodon/Pleroma/Firefish/GoToSocial/Pixelfed/Friendica/Takahē
  - login with Bluesky / ATProto identity and import social graph
  - login with threads.net (requires app verification by Meta)
  - share collections and reviews to Fediverse/Bluesky/Threads
  - ActivityPub support
    - NeoDB users can follow and interact with users on other ActivityPub services like Mastodon and Pleroma
    - NeoDB instances communicate with each other via an extended version of ActivityPub
    - NeoDB instances may share public rating and reviews with a default relay
    - implementation is based on [Takahē](https://jointakahe.org/) server
  - ATProto support
    - NeoDB is not a PDS, but publishes public reviews and ratings to your ATProto repository as structured records
    - a human-readable version is also cross-posted to Bluesky, so other Atmosphere apps may discover it
    - Records from Bookhive and Popfeed are [bridged to NeoDB](https://bridge.neodb.net), so you can see ratings and reviews from those apps


## API
  - Mastodon-compatible API
    - most Mastodon-compatible apps work with NeoDB
  - NeoDB API to manage reviews and collections

## Languages

  - English
  - Simplified Chinese
  - Traditional Chinese
  - more to come and your contributions are welcomed!
