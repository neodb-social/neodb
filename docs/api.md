# API

## Endpoints

NeoDB has a set of API endpoints mapping to its functions, like marking a book or listing collections. They can be found in the Swagger-based API documentation at `/developer/` of your running instance; [a version of it](https://neodb.social/developer/) is available on our flagship instance.

NeoDB also supports a subset of Mastodon API, details can be found in [Mastodon API documentation](https://docs.joinmastodon.org/api/).

Both sets of APIs can be accessed with the same access token.

## How to authorize

### Create an application

You must have at least one URL included in the Redirect URIs field, e.g. `https://example.org/callback`, or use `urn:ietf:wg:oauth:2.0:oob` if you don't have a callback URL.

```
curl https://neodb.social/api/v1/apps \
  -d client_name=MyApp \
  -d redirect_uris=https://example.org/callback \
  -d website=https://my.site
```

and save the `client_id` and `client_secret` returned in the response:

```
{
  "client_id": "CLIENT_ID",
  "client_secret": "CLIENT_SECRET",
  "name": "MyApp",
  "redirect_uri": "https://example.org/callback",
  "vapid_key": "PUSH_KEY",
  "website": "https://my.site"
}
```


### Guide your user to open this URL

```
https://neodb.social/oauth/authorize?response_type=code&client_id=CLIENT_ID&redirect_uri=https://example.org/callback&scope=read+write
```

### Once authorized by the user, it will redirect to `https://example.org/callback` with a `code` parameter:

```
https://example.org/callback?code=AUTH_CODE
```

### Obtain access token with the following POST request:

```
curl https://neodb.social/oauth/token \
	-d "client_id=CLIENT_ID" \
	-d "client_secret=CLIENT_SECRET" \
	-d "code=AUTH_CODE" \
	-d "redirect_uri=https://example.org/callback" \
	-d "grant_type=authorization_code"
```

and an access token will be returned in the response:

```
{
	"access_token": "ACCESS_TOKEN",
	"token_type": "Bearer",
	"scope": "read write"
}
```

### Use the access token to access protected endpoints like `/api/me`

```
curl -H "Authorization: Bearer ACCESS_TOKEN" -X GET https://neodb.social/api/me
```

and the response will be returned accordingly:

```
{
	"username": "xxx",
	"url": "/users/xxx/",
	"display_name": "XYZ",
	"avatar": "https://neodb.social/xxx.gif",
	"external_acct": "xxx@yyy.zzz",
	"external_accounts": [{"platform": "mastodon", "handle": "xxx@yyy.zzz", "url": "https://yyy.zzz/@xxx"}],
	"roles": []
}
```

`url` is relative to the site; `external_acct` is deprecated in favour of
`external_accounts`.

## Webhooks

An application can register one webhook URL per user it is authorized by,
to learn about changes without polling. With the user's access token:

```
curl -H "Authorization: Bearer ACCESS_TOKEN" -X PUT -H "Content-Type: application/json" \
  -d '{"url": "https://example.org/hook"}' https://neodb.social/api/me/webhook
```

`GET /api/me/webhook` returns the current URL and whether it is disabled,
`DELETE /api/me/webhook` removes it. Setting it needs a token with both
`read` and `write` scopes. Only https URLs resolving to public addresses are
accepted, and a user can have at most 5 webhooks across applications.
Once the application holds no token with `read` scope for the user any more
(revoked from the account page, via `/oauth/revoke`, or by logging out
everywhere), its webhook is removed, at the latest when the next change
would have been delivered.

When the user's marks, reviews, notes, collections or articles change,
a JSON document is POSTed to the URL with `Content-Type: application/json`
and a `User-Agent` like `NeoDB/1.0 (+https://neodb.social)`:

```
{
  "version": 1,
  "site": "https://neodb.social",
  "time": "2026-09-07T02:22:47+00:00",
  "username": "alice",
  "changes": [
    {
      "type": "mark",
      "action": "update",
      "object": {
        "shelf_type": "progress",
        "visibility": 0,
        "item": {"uuid": "4upSY7ttUqa5kjvcnXflWt", "title": "Item Title", "...": "..."},
        "comment_text": "...",
        "rating_grade": 8,
        "tags": ["fiction"],
        "...": "..."
      }
    }
  ]
}
```

- `version` is bumped on incompatible changes to this document.
- `username` is the account whose content changed, as in `/api/me`.
- `changes` is a list. Today each delivery carries one entry; consumers
  should nonetheless loop over it. A ping from the developer console sends
  the same document with an empty list.
- `type` is one of `mark`, `review`, `note`, `collection`, `article`;
  `action` is `create`, `update` or `delete`.
- On `create` and `update`, `object` is exactly what the API returns for the
  piece (as in `GET /api/me/shelf/item/{uuid}`, `/api/me/note/item/{uuid}/`,
  `/api/review/{uuid}`, `/api/collection/{uuid}`, `/api/article/{uuid}`),
  minus fields the API documents as deprecated.
- On `delete`, `object` only identifies the piece: `{"uuid": "..."}`, or
  `{"item": {"uuid": "..."}}` for a mark, since marks are addressed by item.

One delivery is sent per change: editing a mark's shelf, comment, rating and
tags together yields one `mark` `update`. Delivery is one attempt, without
retry or signature. After 100 consecutive failures (counted over a week) the
webhook is disabled until it is set again.

Users can see which of their authorized applications have a webhook on the
account page, and set one for the Dev Console token on the developer page.
