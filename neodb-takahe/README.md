***🧩 this repository is part of (and does not work without) [NeoDB](https://neodb.net), please refer to Takahē or Incarnator for a standalone Fediverse micro-blogging server.***

---

Incarnator is a fork of [Takahē](https://github.com/jointakahe/takahe), a fediverse
server for microblogging. You can read more about Takahē from
[the website](https://jointakahe.org/), or check the original
[README](https://github.com/jointakahe/takahe/blob/main/README.md).

This fork has a number of changes/additions:

* Upgraded to pydantic 2.0
* Reworked settings nav to show all identities
* Markers API
* Lists API
* Blocks API
* Languages API
* Featured tags
* Calculated and stored identity stats (post/follow counts)
* Push notifications
* Support for the `notify` attribute on follows
* Hashtag history
* Trending hashtags and statuses
* Fetch follow/post counts for non-local identities
* Relay support
