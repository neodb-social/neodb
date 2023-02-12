// .replace(":id", "")

// GET
const API_FOLLOWERS = "/api/v1/accounts/:id/followers"
// GET
const API_FOLLOWING = "/api/v1/accounts/:id/following"
// GET
const API_GET_ACCOUNT = '/api/v1/accounts/:id'

const NUMBER_PER_REQUEST = 20


// [
//     {
//       "id": "1020382",
//       "username": "atul13061987",
//       "acct": "atul13061987",
//       "display_name": "",
//       "locked": false,
//       "bot": false,
//       "created_at": "2019-12-04T07:17:02.745Z",
//       "note": "<p></p>",
//       "url": "https://mastodon.social/@atul13061987",
//       "avatar": "https://mastodon.social/avatars/original/missing.png",
//       "avatar_static": "https://mastodon.social/avatars/original/missing.png",
//       "header": "https://mastodon.social/headers/original/missing.png",
//       "header_static": "https://mastodon.social/headers/original/missing.png",
//       "followers_count": 0,
//       "following_count": 2,
//       "statuses_count": 0,
//       "last_status_at": null,
//       "emojis": [],
//       "fields": []
//     },
//     {
//       "id": "1020381",
//       "username": "linuxliner",
//       "acct": "linuxliner",
//       "display_name": "",
//       "locked": false,
//       "bot": false,
//       "created_at": "2019-12-04T07:15:56.426Z",
//       "note": "<p></p>",
//       "url": "https://mastodon.social/@linuxliner",
//       "avatar": "https://mastodon.social/avatars/original/missing.png",
//       "avatar_static": "https://mastodon.social/avatars/original/missing.png",
//       "header": "https://mastodon.social/headers/original/missing.png",
//       "header_static": "https://mastodon.social/headers/original/missing.png",
//       "followers_count": 0,
//       "following_count": 2,
//       "statuses_count": 0,
//       "last_status_at": null,
//       "emojis": [],
//       "fields": []
//     }
//   ]
async function getFollowers(id, mastodonURI, token, callback) {
    const url = mastodonURI + API_FOLLOWERS.replace(":id", id);
    var response;
    try {
        response = await fetch(url+'?limit='+NUMBER_PER_REQUEST, {headers: {'Authorization': 'Bearer ' + token}});
    } catch (e) {
        console.error('loading followers failed.');
        return;
    }
    const json = await response.json();
    let nextUrl = null;
    let links = response.headers.get('link');
    if (links) {
        links.split(',').forEach(link => {
            if (link.includes('next')) {
                let regex = /<(.*?)>/;
                nextUrl = link.match(regex)[1];
            }
        });
    }
    callback(json, nextUrl);
}

async function getFollowing(id, mastodonURI, token, callback) {
    const url = mastodonURI + API_FOLLOWING.replace(":id", id);
    var response;
    try {
        response = await fetch(url+'?limit='+NUMBER_PER_REQUEST, {headers: {'Authorization': 'Bearer ' + token}});
    } catch (e) {
        console.error('loading following failed.');
        return;
    }
    const json = await response.json();
    let nextUrl = null;
    let links = response.headers.get('link');
    if (links) {
        links.split(',').forEach(link => {
            if (link.includes('next')) {
                let regex = /<(.*?)>/;
                nextUrl = link.match(regex)[1];
            }
        });
    }
    callback(json, nextUrl);
}

function getUserInfo(id, mastodonURI, token, callback) {
    let url = mastodonURI + API_GET_ACCOUNT.replace(":id", id);
    fetch(url, {headers: {'Authorization': 'Bearer ' + token}}).then((response) => response.json()).then(callback);
    // $.ajax({
    //     url: url,
    //     method: 'GET',
    //     headers: {
    //         'Authorization': 'Bearer ' + token,
    //     },
    //     success: function(data){
    //         callback(data);
    //     },
    // });
}

function getEmojiDict(emoji_list) {
    let dict = new Object;
    emoji_list.forEach(pair => {
        dict[":" + pair.shortcode + ":"] = pair.url;
    });
    return dict;
}

function translateEmojis(text, emoji_list, large) {
    let dict = getEmojiDict(emoji_list);
    let regex = /:(.*?):/g;
    let translation = null
    if (large) {
        translation = text.replace(regex, function (match) {
            return "<img src=" + dict[match] + " class=emoji--large alt=" + match + ">";
        });
    } else {
        translation = text.replace(regex, function (match) {
            return "<img src=" + dict[match] + " class=emoji alt=" + match + ">";
        });
    }
    return translation;
}
