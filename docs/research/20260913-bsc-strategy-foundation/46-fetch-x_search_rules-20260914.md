## Documentation Index

Fetch the complete documentation index at: </llms.txt>

Use this file to discover all available pages before exploring further.

![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)
![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)
![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)
![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)
![](https://mintlify.s3.us-west-1.amazonaws.com/x-preview/icons/xds/icon-brackets.svg)

General

![](https://mintlify.s3.us-west-1.amazonaws.com/x-preview/icons/xds/icon-console.svg)
![](https://mintlify.s3.us-west-1.amazonaws.com/x-preview/icons/xds/icon-chat.svg)

### Overview

### Posts

### Articles

### Users

### Direct Messages

### Likes

### Lists

### Spaces

### Communities

### Community Notes

### Trends

### News

### Media

### X Activity

### Usage

### Stream Connections

### Compliance

### Webhooks

# Recent Search Quickstart

This guide walks you through making your first recent search request to find Posts from the. Reference for the X API v2 standard tier covering quickstart.

![](https://mintcdn.com/x-preview/cfyQtgCdwk8p69aa/icons/xds/icon-search.svg?fit=max&auto=format&n=cfyQtgCdwk8p69aa&q=85&s=8c11ad89387b7c09ced1553d5c232834)

Build a query

`python`
`python lang:en -is:retweet`

Make a request

shellscript

`curl "https://api.x.com/2/tweets/search/recent?query=python%20lang%3Aen%20-is%3Aretweet" \
 -H "Authorization: Bearer $BEARER_TOKEN"`
`from xdk import Client

client = Client(bearer_token="YOUR_BEARER_TOKEN")

# Search recent Posts
for page in client.posts.search_recent(
 query="python lang:en -is:retweet"
):
 for post in page.data:
 print(post.text)`
`import { Client } from "@xdevplatform/xdk";

const client = new Client({ bearerToken: "YOUR_BEARER_TOKEN" });

// Search recent Posts
const paginator = client.posts.searchRecent({
 query: "python lang:en -is:retweet",
});

for await (const page of paginator) {
 page.data?.forEach((post) => {
 console.log(post.text);
 });
}`

Review the response

`id`
`text`
`edit_history_tweet_ids`
`{
 "data": [
 {
 "id": "1234567890123456789",
 "text": "Just started learning Python and loving it!",
 "edit_history_tweet_ids": ["1234567890123456789"]
 },
 {
 "id": "1234567890123456788",
 "text": "Python tip: use list comprehensions for cleaner code",
 "edit_history_tweet_ids": ["1234567890123456788"]
 }
 ],
 "meta": {
 "newest_id": "1234567890123456789",
 "oldest_id": "1234567890123456788",
 "result_count": 2
 }
}`

Add fields and expansions

shellscript

`curl "https://api.x.com/2/tweets/search/recent?\
query=python%20lang%3Aen%20-is%3Aretweet&\
tweet.fields=created_at,public_metrics,author_id&\
expansions=author_id&\
user.fields=username,verified&\
max_results=10" \
 -H "Authorization: Bearer $BEARER_TOKEN"`
`from xdk import Client

client = Client(bearer_token="YOUR_BEARER_TOKEN")

# Search with fields and expansions
for page in client.posts.search_recent(
 query="python lang:en -is:retweet",
 tweet_fields=["created_at", "public_metrics", "author_id"],
 expansions=["author_id"],
 user_fields=["username", "verified"],
 max_results=10
):
 for post in page.data:
 print(f"{post.text[:50]}... - Likes: {post.public_metrics.like_count}")`
`import { Client } from "@xdevplatform/xdk";

const client = new Client({ bearerToken: "YOUR_BEARER_TOKEN" });

// Search with fields and expansions
const paginator = client.posts.searchRecent({
 query: "python lang:en -is:retweet",
 tweetFields: ["created_at", "public_metrics", "author_id"],
 expansions: ["author_id"],
 userFields: ["username", "verified"],
 maxResults: 10,
});

for await (const page of paginator) {
 page.data?.forEach((post) => {
 console.log(`${post.text?.slice(0, 50)}... - Likes: ${post.public_metrics?.like_count}`);
 });
}`
![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-brackets.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=ed2428e77bab43e57800e1a590e982fa)
`{
 "data": [
 {
 "id": "1234567890123456789",
 "text": "Just started learning Python and loving it!",
 "created_at": "2024-01-15T10:30:00.000Z",
 "author_id": "9876543210",
 "public_metrics": {
 "retweet_count": 5,
 "reply_count": 2,
 "like_count": 42,
 "quote_count": 1
 },
 "edit_history_tweet_ids": ["1234567890123456789"]
 }
 ],
 "includes": {
 "users": [
 {
 "id": "9876543210",
 "username": "pythondev",
 "verified": false
 }
 ]
 },
 "meta": {
 "newest_id": "1234567890123456789",
 "oldest_id": "1234567890123456789",
 "result_count": 1
 }
}`
![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-arrow-right.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=88e933002782dbdeb204043cedef033e)

Paginate through results

`next_token`
`curl "https://api.x.com/2/tweets/search/recent?\
query=python&\
max_results=100&\
next_token=b26v89c19zqg8o3fo7gesq314yb9l2l4ptqy" \
 -H "Authorization: Bearer $BEARER_TOKEN"`
![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-arrow-right.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=88e933002782dbdeb204043cedef033e)

## Pagination guide

## [​](#example-queries) Example queries

Posts from a specific user

`from:XDevelopers`

Posts with a hashtag

`#Python -is:retweet`

Posts with images

`"machine learning" has:images lang:en`

Posts mentioning a user

`@elonmusk -is:retweet -is:reply`

Posts with links to a domain

`url:github.com lang:en`

## [​](#next-steps) Next steps

![](https://mintcdn.com/x-preview/cfyQtgCdwk8p69aa/icons/xds/icon-search.svg?fit=max&auto=format&n=cfyQtgCdwk8p69aa&q=85&s=8c11ad89387b7c09ced1553d5c232834)

## Build a query

## Operator reference

## Full-archive search

![](https://mintcdn.com/x-preview/ygI6sSJPehlc0qNT/icons/xds/icon-code.svg?fit=max&auto=format&n=ygI6sSJPehlc0qNT&q=85&s=488e23401b19225b89acc0136d242219)

## API Reference

## On this page

![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)
![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)

Resources

Legal
