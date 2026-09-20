> ## Documentation Index
>
> Fetch the complete documentation index at:</llms.txt>
>
> Use this file to discover all available pages before exploring further.

[X home page![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)](/)

* [Support](/support)
* [Developer Console](https://developer.x.com/en/portal/petition/essential/basic-info)

[X home page![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)](/)

Filtered Stream

# Filtered Stream

Receive near real-time Posts that match custom rules with X API v2 Filtered Stream, using powerful operators to filter the public Post firehose.

The Filtered Stream endpoints let you receive near real-time Posts that match your filter rules. Create rules using powerful operators, then connect to a persistent stream to receive matching Posts as they’re published.

Filtered Stream prioritizes data hydration and delivery, with approximately 6-7 seconds of P99 latency. For lower latency requirements, see [Powerstream](/x-api/powerstream/introduction).

## [​](#overview) Overview

## Near real-time delivery

Receive Posts within seconds of publication

![](https://mintcdn.com/x-preview/szd6PKNMlRQoyyAo/icons/xds/icon-filter.svg?fit=max&auto=format&n=szd6PKNMlRQoyyAo&q=85&s=5d59aff402c1f2aeae0e9e44bb23400e)

## Persistent rules

Add and remove rules without disconnecting

![](https://mintcdn.com/x-preview/cfyQtgCdwk8p69aa/icons/xds/icon-search.svg?fit=max&auto=format&n=cfyQtgCdwk8p69aa&q=85&s=8c11ad89387b7c09ced1553d5c232834)

## Powerful operators

Match on keywords, hashtags, users, and more

## Webhook delivery

Optionally receive Posts via webhooks

---

## [​](#how-it-works) How it works

1. **Create rules** — Define filter rules using operators
2. **Connect to stream** — Establish a persistent HTTP connection
3. **Receive Posts** — Get matching Posts in near real-time

---

## [​](#endpoints) Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | [`/2/tweets/search/stream`](/x-api/stream/stream-filtered-posts) | Connect to the stream |
| POST | [`/2/tweets/search/stream/rules`](/x-api/stream/update-stream-rules) | Add or delete rules |
| GET | [`/2/tweets/search/stream/rules`](/x-api/stream/get-stream-rules) | List current rules |

---

## [​](#access-levels) Access levels

| Feature | Pay-per-use | Enterprise |
| --- | --- | --- |
| Rules per project | 1,000 | 25,000+ |
| Rule length | 1,024 chars | 2,048 chars |
| Connections | 1 | Multiple |
| Core operators | ✓ | ✓ |
| Semantic embedding operators | — | ✓ (requires Embedding tier) |

![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-bank.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=6dd9ad48fa88936abb112b49e022abff)

## Contact for Enterprise

Get higher limits and additional features

Some operators require specific tiers. The `embedding:` semantic operator is available **only for Filtered Stream** on Enterprise plans with Embedding tier access. Standard Pay-per-use access includes core operators only.

---

## [​](#building-rules) Building rules

Rules use the same operators as search queries:

```
(AI OR "machine learning") lang:en -is:retweet(AI OR "machine learning") lang:en -is:retweet 
```

### [​](#example-rules) Example rules

| Rule | Matches |
| --- | --- |
| `#python` | Posts with #python hashtag |
| `from:elonmusk` | Posts by @elonmusk |
| `"breaking news" has:images` | Posts with phrase and images |
| `(@XDevelopers OR @X) -is:retweet` | Mentions, excluding retweets |
| `embedding:"climate change policy"` | Posts semantically about climate policy (Enterprise + Embedding tier) |

![](https://mintcdn.com/x-preview/szd6PKNMlRQoyyAo/icons/xds/icon-filter.svg?fit=max&auto=format&n=szd6PKNMlRQoyyAo&q=85&s=5d59aff402c1f2aeae0e9e44bb23400e)

## Build a rule

Learn rule syntax and operators

---

## [​](#connecting-to-the-stream) Connecting to the stream

Establish a persistent HTTP connection to receive Posts:

```
import requests import  requests def stream_posts(bearer_token): def  stream_posts(bearer_token): url = "https://api.x.com/2/tweets/search/stream"  url = "https://api.x.com/2/tweets/search/stream" headers = {"Authorization": f"Bearer {bearer_token}"}  headers = {"Authorization": f "Bearer {bearer_token} "}    response = requests.get(url, headers=headers, stream=True)  response = requests.get(url, headers =headers, stream = True)    for line in response.iter_lines():  for  line in response.iter_lines(): if line:  if line: print(line.decode("utf-8"))  print(line.decode("utf-8"))
```

### [​](#keep-alive-signals) Keep-alive signals

The stream sends blank lines (`\r\n`) every 20 seconds to maintain the connection. If you don’t receive data or a keep-alive for 20 seconds, reconnect.

## Handling disconnections

Reconnect gracefully

## Consuming streaming data

Process Posts efficiently

---

## [​](#webhook-delivery) Webhook delivery

Instead of maintaining a persistent connection, you can receive Posts via webhooks:

## Webhook delivery

Set up webhook delivery for filtered stream

---

## [​](#post-edits) Post edits

The stream delivers edited Posts with their edit history. Each edit creates a new Post ID:

```
{{ "data": { "data": { "id": "1234567893",  "id": "1234567893", "text": "Hello world! (edited)",  "text": "Hello world! (edited)", "edit_history_tweet_ids": ["1234567890", "1234567891", "1234567893"]  "edit_history_tweet_ids": ["1234567890", "1234567891", "1234567893"] } }}}
```

![](https://mintcdn.com/x-preview/szd6PKNMlRQoyyAo/icons/xds/icon-history.svg?fit=max&auto=format&n=szd6PKNMlRQoyyAo&q=85&s=6afe17587c08ee621e37afde19a07ff1)

## Edit Posts fundamentals

Learn more about Post edits

---

## [​](#getting-started) Getting started

**Prerequisites**

* An approved [developer account](https://developer.x.com/en/portal/petition/essential/basic-info)
* A [Project and App](/resources/fundamentals/developer-apps) in the Developer Console
* Your App’s [Bearer Token](/resources/fundamentals/authentication)

![](https://mintcdn.com/x-preview/oR-aRNyj1BKPJtxM/icons/xds/icon-rocket.svg?fit=max&auto=format&n=oR-aRNyj1BKPJtxM&q=85&s=b978d7a9225de31709efbbed5b84e92d)

## Quickstart

Connect to the stream in minutes

![](https://mintcdn.com/x-preview/szd6PKNMlRQoyyAo/icons/xds/icon-filter.svg?fit=max&auto=format&n=szd6PKNMlRQoyyAo&q=85&s=5d59aff402c1f2aeae0e9e44bb23400e)

## Build a rule

Learn rule syntax

## Operator reference

All available operators

## Sample code

Working code examples

---

## [​](#advanced-topics) Advanced topics

## Handling disconnections

Reconnect gracefully

## High volume capacity

Handle high throughput

![](https://mintcdn.com/x-preview/cfyQtgCdwk8p69aa/icons/xds/icon-shield-keyhole.svg?fit=max&auto=format&n=cfyQtgCdwk8p69aa&q=85&s=a0e05514090c8a6af232297bfb9c4055)

## Recovery and redundancy

Build resilient applications

## Matching returned Posts

Identify which rules matched
