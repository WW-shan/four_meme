> ## Documentation Index
>
> Fetch the complete documentation index at:</llms.txt>
>
> Use this file to discover all available pages before exploring further.

[X home page![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)](/)

* [Support](/support)
* [Developer Console](https://developer.x.com/en/portal/petition/essential/basic-info)

[X home page![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)](/)

Fundamentals

# Fields

The X API v2 returns minimal data by default. Use fields parameters to request additional data for each object type. By default, a post lookup returns only.

The X API v2 returns minimal data by default. Use **fields parameters** to request additional data for each object type. 

---

## [​](#how-fields-work) How fields work

By default, a post lookup returns only `id`, `text`, and `edit_history_tweet_ids`. To get more data, add field parameters to your request:

```
# Default response - minimal fields# Default response - minimal fieldscurl "https://api.x.com/2/tweets/1234567890" \ curl "https://api.x.com/2/tweets/1234567890"  \ -H "Authorization: Bearer $TOKEN" -H "Authorization: Bearer $TOKEN " # With additional fields # With additional fieldscurl "https://api.x.com/2/tweets/1234567890?tweet.fields=created_at,public_metrics,author_id" \ curl "https://api.x.com/2/tweets/1234567890?tweet.fields=created_at,public_metrics,author_id"  \ -H "Authorization: Bearer $TOKEN" -H "Authorization: Bearer $TOKEN "
```

---

## [​](#available-field-parameters) Available field parameters

Each object type has its own fields parameter:

| Object | Parameter | Documentation |
| --- | --- | --- |
| Post (Tweet) | `tweet.fields` | [Post fields](/x-api/fundamentals/data-dictionary/reference#tweet) |
| User | `user.fields` | [User fields](/x-api/fundamentals/data-dictionary/reference#user) |
| Media | `media.fields` | [Media fields](/x-api/fundamentals/data-dictionary/reference#media) |
| Poll | `poll.fields` | [Poll fields](/x-api/fundamentals/data-dictionary/reference#poll) |
| Place | `place.fields` | [Place fields](/x-api/fundamentals/data-dictionary/reference#place) |

---

## [​](#example-post-fields) Example: Post fields

Request specific post fields with `tweet.fields`:

```
curl "https://api.x.com/2/tweets/1234567890?tweet.fields=created_at,public_metrics,lang" \ curl "https://api.x.com/2/tweets/1234567890?tweet.fields=created_at,public_metrics,lang"  \ -H "Authorization: Bearer $TOKEN" -H "Authorization: Bearer $TOKEN "
```

Response:

![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-brackets.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=ed2428e77bab43e57800e1a590e982fa)Example response

```
{{ "data": { "data": { "id": "1234567890",  "id": "1234567890", "text": "Hello world!",  "text": "Hello world!", "edit_history_tweet_ids": ["1234567890"],  "edit_history_tweet_ids": ["1234567890"], "created_at": "2024-01-15T12:00:00.000Z",  "created_at": "2024-01-15T12:00:00.000Z", "lang": "en",  "lang": "en", "public_metrics": { "public_metrics": { "retweet_count": 10,  "retweet_count": 10, "reply_count": 5,  "reply_count": 5, "like_count": 100,  "like_count": 100, "quote_count": 2,  "quote_count": 2, "bookmark_count": 3,  "bookmark_count": 3, "impression_count": 1500  "impression_count": 1500 } } } }}}
```

---

## [​](#example-user-fields) Example: User fields

Request specific user fields with `user.fields`:

```
curl "https://api.x.com/2/users/by/username/xdevelopers?user.fields=created_at,description,public_metrics" \ curl "https://api.x.com/2/users/by/username/xdevelopers?user.fields=created_at,description,public_metrics"  \ -H "Authorization: Bearer $TOKEN" -H "Authorization: Bearer $TOKEN "
```

Response:

![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-brackets.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=ed2428e77bab43e57800e1a590e982fa)Example response

```
{{ "data": { "data": { "id": "2244994945",  "id": "2244994945", "name": "X Developers",  "name": "X Developers", "username": "xdevelopers",  "username": "xdevelopers", "created_at": "2013-12-14T04:35:55.000Z",  "created_at": "2013-12-14T04:35:55.000Z", "description": "The voice of the X Developer Platform",  "description": "The voice of the X Developer Platform", "public_metrics": { "public_metrics": { "followers_count": 570842,  "followers_count": 570842, "following_count": 2048,  "following_count": 2048, "tweet_count": 14052,  "tweet_count": 14052, "listed_count": 1672  "listed_count": 1672 } } } }}}
```

---

## [​](#fields-for-related-objects) Fields for related objects

To get fields on related objects (like the author of a post), you need two things:

1. An **expansion** to include the related object
2. The **fields parameter** for that object type

```
# Get post with author details # Get post with author detailscurl "https://api.x.com/2/tweets/1234567890?expansions=author_id&user.fields=description,public_metrics" \ curl "https://api.x.com/2/tweets/1234567890?expansions=author_id&user.fields=description,public_metrics"  \ -H "Authorization: Bearer $TOKEN" -H "Authorization: Bearer $TOKEN "
```

Response:

![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-brackets.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=ed2428e77bab43e57800e1a590e982fa)Example response

```
{{ "data": { "data": { "id": "1234567890",  "id": "1234567890", "text": "Hello world!",  "text": "Hello world!", "author_id": "2244994945"  "author_id": "2244994945" }, }, "includes": { "includes": { "users": [{ "users": [{ "id": "2244994945",  "id": "2244994945", "name": "X Developers",  "name": "X Developers", "username": "xdevelopers",  "username": "xdevelopers", "description": "The voice of the X Developer Platform",  "description": "The voice of the X Developer Platform", "public_metrics": { "public_metrics": { "followers_count": 570842,  "followers_count": 570842, "following_count": 2048  "following_count": 2048 } } }] }] } }}}
```

[Learn more about expansions →](/x-api/fundamentals/expansions) 

---

## [​](#common-field-combinations) Common field combinations

* Post analytics
* User profiles
* Full post context
* Media details

---

## [​](#important-notes) Important notes

**You cannot request subfields.** When you request `public_metrics`, you get all metrics (likes, reposts, replies, quotes, bookmarks, impressions). You can’t request just `public_metrics.like_count`.

* Field order in responses may differ from request order
* Missing fields in responses mean the value is `null` or empty
* Some fields require specific authentication (e.g., private metrics need user context)
* Check each endpoint’s API reference for available fields

---

## [​](#next-steps) Next steps

## Expansions

Include related objects in responses.

![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-book.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=22ac564792481d14ae36a941546039c8)

## Data Dictionary

Complete field reference for all objects.
