# Federation

## Supported federation protocols and standards

- [ActivityPub](https://www.w3.org/TR/activitypub/) (Server-to-Server)
- [WebFinger](https://webfinger.net/)
- [Http Signatures](https://datatracker.ietf.org/doc/html/draft-cavage-http-signatures)
- [NodeInfo](https://nodeinfo.diaspora.software/)

## Supported FEPs

- [FEP-f1d5: NodeInfo in Fediverse Software](https://codeberg.org/fediverse/fep/src/branch/main/fep/f1d5/fep-f1d5.md)

## NodeInfo

NeoDB instances can be identified from user agent string (`NeoDB/x.x (+https://example.org)`) and `protocols` in its nodeinfo, e.g. https://neodb.social/nodeinfo/2.0/ :
```json
{
  "version": "2.0",
  "software": {
    "name": "neodb",
    "version": "0.10.4.13",
    "repository": "https://github.com/neodb-social/neodb",
    "homepage": "https://neodb.net/"
  },
  "protocols": ["activitypub", "neodb"],
}
```


## ActivityPub

NeoDB's ActivityPub implementation is based on [Takahē](https://jointakahe.org), with some change to enable interchange of additional information between NeoDB instances.

### Activity

NeoDB add additional fields to `Note` activity:

  - `relatedWith` is a list of NeoDB specific activities which are associated with this `Note`. For each activity, `id` and `href` are both unique links to that activity, `withRegardTo` links to the catalog item, `attributedTo` links to the user, `type` is one of:
    - `Status`, its `status` can be one of: `complete`, `progress`, `wishlist` and `dropped`
    - `Rating`, its `value` is rating grade (int, 1-10), `worst` is always 1, `best` is always 10
    - `Comment`, its `content` is comment text
    - `Review`, its `name` is review title, `content` is its body, `mediaType` is always `text/markdown` for now
    - `Note`, its `content` is note text
  - `tag` is used to store list of NeoDB catalog items, which are related with this activity. `type` of NeoDB catalog item can be one of `Edition`, `Movie`, `TVShow`, `TVSeason`, `TVEpisode`, `Album`, `Game`, `Podcast`, `PodcastEpisode`, `Performance`, `PerformanceProduction`; href will be the link to that item.

Example:
```json
{
  "@context": ["https://www.w3.org/ns/activitystreams", {
    "blurhash": "toot:blurhash",
    "Emoji": "toot:Emoji",
    "focalPoint": {
      "@container": "@list",
      "@id": "toot:focalPoint"
    },
    "Hashtag": "as:Hashtag",
    "manuallyApprovesFollowers": "as:manuallyApprovesFollowers",
    "sensitive": "as:sensitive",
    "toot": "http://joinmastodon.org/ns#",
    "votersCount": "toot:votersCount",
    "featured": {
      "@id": "toot:featured",
      "@type": "@id"
    }
  }, "https://w3id.org/security/v1"],
  "id": "https://neodb.social/@april_long_face@neodb.social/posts/380919151408919488/",
  "type": "Note",
  "relatedWith": [{
    "id": "https://neodb.social/p/5oyF0qRx96mKKmVpFzHtMM",
    "type": "Status",
    "status": "complete",
    "withRegardTo": "https://neodb.social/movie/7hfF7d0aFMaqHpFjUpq4zR",
    "attributedTo": "https://neodb.social/@april_long_face@neodb.social/",
    "href": "https://neodb.social/p/5oyF0qRx96mKKmVpFzHtMM",
    "published": "2024-11-17T10:16:42.745240+00:00",
    "updated": "2024-11-17T10:16:42.750917+00:00"
  }, {
    "id": "https://neodb.social/p/47cJnbQTkbSSN2izLwQMjo",
    "type": "Comment",
    "withRegardTo": "https://neodb.social/movie/7hfF7d0aFMaqHpFjUpq4zR",
    "attributedTo": "https://neodb.social/@april_long_face@neodb.social/",
    "content": "Broadway cin\u00e9math\u00e8que, at least I laughed hard.",
    "href": "https://neodb.social/p/47cJnbQTkbSSN2izLwQMjo",
    "published": "2024-11-17T10:16:42.745240+00:00",
    "updated": "2024-11-17T10:16:42.777276+00:00"
  }, {
    "id": "https://neodb.social/p/3AyYu974qo6OU09AAsPweQ",
    "type": "Rating",
    "best": 10,
    "value": 7,
    "withRegardTo": "https://neodb.social/movie/7hfF7d0aFMaqHpFjUpq4zR",
    "worst": 1,
    "attributedTo": "https://neodb.social/@april_long_face@neodb.social/",
    "href": "https://neodb.social/p/3AyYu974qo6OU09AAsPweQ",
    "published": "2024-11-17T10:16:42.784220+00:00",
    "updated": "2024-11-17T10:16:42.786458+00:00"
  }],
  "attributedTo": "https://neodb.social/@april_long_face@neodb.social/",
  "content": "<p>\u770b\u8fc7 <a href=\"https://neodb.social/~neodb~/movie/7hfF7d0aFMaqHpFjUpq4zR\" rel=\"nofollow\">\u963f\u8bfa\u62c9</a> \ud83c\udf15\ud83c\udf15\ud83c\udf15\ud83c\udf17\ud83c\udf11  <br>Broadway cin\u00e9math\u00e8que, at least I laughed hard.</p><p><a href=\"https://neodb.social/tags/\u6211\u770b\u6211\u542c\u6211\u8bfb/\" class=\"mention hashtag\" rel=\"tag\">#\u6211\u770b\u6211\u542c\u6211\u8bfb</a></p>",
  "published": "2024-11-17T10:16:42.745Z",
  "sensitive": false,
  "tag": [{
    "type": "Hashtag",
    "href": "https://neodb.social/tags/\u6211\u770b\u6211\u542c\u6211\u8bfb/",
    "name": "#\u6211\u770b\u6211\u542c\u6211\u8bfb"
  }, {
    "type": "Movie",
    "href": "https://neodb.social/movie/7hfF7d0aFMaqHpFjUpq4zR",
    "image": "https://neodb.social/m/item/doubanmovie/2024/09/13/a30bf2f3-4f79-43ef-b22f-58ebc3fd8aae.jpg",
    "name": "Anora"
  }],
  "to": ["https://www.w3.org/ns/activitystreams#Public"],
  "updated": "2024-11-17T10:16:42.750Z",
  "url": "https://neodb.social/@april_long_face/posts/380919151408919488/"
}
```

This is not ideal but a practical manner to pass along additional information between NeoDB instances and other ActivityPub servers. We have some ideas for improvements, but are open to more suggestions.


### Relay

NeoDB instances may share public rating and reviews with a default relay, which is currently `https://relay.neodb.net`. This relay is used to propagate public activities and catalog information between NeoDB instances.

Owner of each instance may choose to turn this off in their admin settings.


## ATProto

NeoDB is not a PDS itself currently, but can interact with PDS to import user's social graph, and send status updates. So technically NeoDB does not do full federation in ATProto, but NeoDB will handle some side effect from federation, e.g. when user logging in via ATProto handle, NeoDB will resolve user's DID and store it, and will attempt further operation with the DID, and update user's handle if that's changed, and use the corresponding PDS for that handle; user may still have to login NeoDB again with their Bluesky app password, since the change of PDS may invalidates previous app password.
