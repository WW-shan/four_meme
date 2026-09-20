> ## Documentation Index
>
> Fetch the complete documentation index at:</llms.txt>
>
> Use this file to discover all available pages before exploring further.

[X home page![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)](/)

* [Support](/support)
* [Developer Console](https://developer.x.com/en/portal/petition/essential/basic-info)

[X home page![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)](/)

Search

# Search Posts

The Search Posts endpoints let you find Posts matching specific criteria using powerful query. Reference for the X API v2 standard tier covering search.

The Search Posts endpoints let you find Posts matching specific criteria using powerful query operators. Search for keywords, hashtags, mentions, URLs, and more.

## [​](#overview) Overview

X offers two search endpoints with different time ranges and access requirements:

## Recent Search

Search Posts from the **last 7 days**. Available to all developers.

## Full-Archive Search

Search the **complete archive** back to 2006. Available to pay-per-use and Enterprise customers.

---

## [​](#use-cases) Use cases

* **Brand monitoring** — Track mentions of your brand or products
* **Trend analysis** — Analyze conversations around topics or events
* **Research** — Gather data for academic or market research
* **Real-time listening** — Build applications that react to new Posts

---

## [​](#endpoints) Endpoints

| Method | Endpoint | Description | Access |
| --- | --- | --- | --- |
| GET | [`/2/tweets/search/recent`](/x-api/posts/search-recent-posts) | Search Posts from last 7 days | All developers |
| GET | [`/2/tweets/search/all`](/x-api/posts/search-all-posts) | Search complete Post archive | Pay-per-use, Enterprise |

---

## [​](#query-operators) Query operators

Build queries using operators that match on Post attributes:

```
(AI OR "artificial intelligence") lang:en -is:retweet has:links(AI OR "artificial intelligence") lang:en -is:retweet has:links 
```

Keyword operators

| Operator | Example | Description |
| --- | --- | --- |
| Keyword | `python` | Match Posts containing the word |
| Phrase | `"machine learning"` | Match exact phrase |
| Hashtag | `#AI` | Match Posts with hashtag |
| Mention | `@XDevelopers` | Match Posts mentioning user |

User operators

| Operator | Example | Description |
| --- | --- | --- |
| `from:` | `from:elonmusk` | Posts by a user |
| `to:` | `to:XDevelopers` | Replies to a user |
| `retweets_of:` | `retweets_of:X` | Retweets of a user’s Posts |

Content operators

| Operator | Example | Description |
| --- | --- | --- |
| `has:images` | `cat has:images` | Posts with images |
| `has:videos` | `has:videos` | Posts with videos |
| `has:links` | `has:links` | Posts with links |
| `has:mentions` | `has:mentions` | Posts with mentions |
| `url:` | `url:github.com` | Posts with specific URL |

Filter operators

| Operator | Example | Description |
| --- | --- | --- |
| `lang:` | `lang:en` | Posts in a language |
| `-is:retweet` | `-is:retweet` | Exclude retweets |
| `-is:reply` | `-is:reply` | Exclude replies |
| `is:verified` | `is:verified` | Posts by verified users |

## Full operator reference

See all available operators and their access requirements

---

## [​](#recent-search) Recent Search

Search Posts from the **last 7 days**. Available to all developers.

### [​](#features) Features

* Up to 100 Posts per request
* Pagination for large result sets
* All core query operators
* 512-character query length (4,096 for Enterprise)

![](https://mintcdn.com/x-preview/oR-aRNyj1BKPJtxM/icons/xds/icon-rocket.svg?fit=max&auto=format&n=oR-aRNyj1BKPJtxM&q=85&s=b978d7a9225de31709efbbed5b84e92d)

## Quickstart

Make your first recent search request

![](https://mintcdn.com/x-preview/ygI6sSJPehlc0qNT/icons/xds/icon-code.svg?fit=max&auto=format&n=ygI6sSJPehlc0qNT&q=85&s=488e23401b19225b89acc0136d242219)

## API Reference

Full endpoint documentation

---

## [​](#full-archive-search) Full-Archive Search

Search the **complete Post archive** dating back to March 2006.

Full-archive search is available to pay-per-use and Enterprise customers.

### [​](#features-2) Features

* Up to 500 Posts per request
* Access to complete Post history
* All query operators available
* 1,024-character query length (4,096 for Enterprise)

![](https://mintcdn.com/x-preview/oR-aRNyj1BKPJtxM/icons/xds/icon-rocket.svg?fit=max&auto=format&n=oR-aRNyj1BKPJtxM&q=85&s=b978d7a9225de31709efbbed5b84e92d)

## Quickstart

Make your first full-archive search request

![](https://mintcdn.com/x-preview/ygI6sSJPehlc0qNT/icons/xds/icon-code.svg?fit=max&auto=format&n=ygI6sSJPehlc0qNT&q=85&s=488e23401b19225b89acc0136d242219)

## API Reference

Full endpoint documentation

---

## [​](#getting-started) Getting started

**Prerequisites**

* An approved [developer account](https://developer.x.com/en/portal/petition/essential/basic-info)
* A [Project and App](/resources/fundamentals/developer-apps) in the Developer Console
* Your App’s [keys and tokens](/resources/fundamentals/authentication)

![](https://mintcdn.com/x-preview/cfyQtgCdwk8p69aa/icons/xds/icon-search.svg?fit=max&auto=format&n=cfyQtgCdwk8p69aa&q=85&s=8c11ad89387b7c09ced1553d5c232834)

## Build a query

Learn query syntax and operators

![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-arrow-right.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=88e933002782dbdeb204043cedef033e)

## Pagination

Navigate through large result sets

![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-book.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=22ac564792481d14ae36a941546039c8)

## Integration guide

Key concepts and best practices

## Sample code

Working code examples
