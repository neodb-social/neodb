# Servers


## Community instances

NeoDB is not a single website. To use it, you need to sign up on an instance that lets you connect with other people using NeoDB across the Fediverse and Bluesky.

{servers}

A JSON version of this list is also available [here](servers.json). If you are hosting a public instance of NeoDB and wish to share that with the community, please [edit this file](https://github.com/neodb-social/neodb/edit/main/docs/servers.json) and submit a pull request.

To host your own instance of NeoDB, see [installation guide](install.md).


## Public relay and bridge hosted by NeoDB developers

 - `relay.neodb.net` - NeoDB instances may connect to this [open sourced](https://github.com/neodb-social/neodb-relay) relay server to send and receive public posts; this helps share catalogs, ratings and reviews across the Fediverse. It works the same way as most ActivityPub relays, except it only relays between compatible NeoDB instances. If you don't want to relay public posts with other NeoDB instances, turn it off in [configuration](configuration.md).
 - `bridge.neodb.net` - This server translates records from Bookhive and Popfeed to relay.neodb.net, so NeoDB instances may see ratings and reviews from those apps in the Atmosphere.

## Honorable mention
 - [NiceDB](https://nicedb.org) - the original instance, no longer open for registration.
