# API

## Endpoints

NeoDB has a set of API endpoints mapping to its functions like marking a book or listing collections, they can be found in swagger based API documentation at `/developer/` of your running instance, [a version of it](https://neodb.social/developer/) is available on our flagship instance.

NeoDB also supports a subset of Mastodon API, details can be found in [Mastodon API documentation](https://docs.joinmastodon.org/api/).

Both set of APIs can be accessed by the same access token.

## How to authorize

### Create an application

you must have at least one URL included in the Redirect URIs field, e.g. `https://example.org/callback`, or use `urn:ietf:wg:oauth:2.0:oob` if you don't have a callback URL.

```
curl https://neodb.social/api/v1/apps \
  -d client_name=MyApp \
  -d redirect_uris=https://example.org/callback \
  -d website=https://my.site
```

and save of the `client_id` and `client_secret` returned in the response:

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

### Once authorizated by user, it will redirect to `https://example.org/callback` with a `code` parameter:

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

and access token will be returned in the response:

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

and response will be returned accordingly:

```
{"url": "https://neodb.social/users/xxx/", "external_acct": "xxx@yyy.zzz", "display_name": "XYZ", "avatar": "https://yyy.zzz/xxx.gif"}
```
