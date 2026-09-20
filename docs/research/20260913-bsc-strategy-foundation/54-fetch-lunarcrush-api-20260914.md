# Build with LunarCrush: API, MCP, and SDKs

The LunarCrush API v4 is a JSON-over-HTTPS API rooted at`https://lunarcrush.com/api4`. Authenticate every request with an `Authorization: Bearer`  header.

## What the API returns

One API covers social sentiment, mention and engagement volume, creators, topics, trending data, and price and market metrics for both crypto and stocks. Endpoints are organized into 9groups:

* AI & MCP Server
* Topics
* Categories
* Creators
* Posts
* Coins
* Stocks
* Searches
* System

For example, `/public/coins/list/v1` returns every tracked coin with price, market cap, Galaxy Score, AltRank, sentiment, and 24-hour social volume; `/public/topic//posts/v1` returns the top social posts for a topic; and `/public/category//creators/v1` returns the most influential creators in a category.

## Authentication

All requests use HTTPS and return JSON. Authenticate every call with an`Authorization: Bearer`  header. The base URL is `https://lunarcrush.com/api4`. The same key powers the webapp, the CLI, and the MCP server. You can hold up to five active keys at once; generate, label, rotate, and revoke them on the [API Keys page](/developers/keys). Keep keys secret — if one leaks, delete it and generate a new one.

## Code samples

Fetch the list of tracked coins with curl:

```
curl -H "Authorization: Bearer " \ "https://lunarcrush.com/api4/public/coins/list/v1"
```

Or with JavaScript fetch:

```
const res = await fetch( "https://lunarcrush.com/api4/public/coins/list/v1", { headers: { Authorization: "Bearer " } }, ); const data = await res.json(); console.log(data);
```

The reference also ships ready-to-run samples in Node.js (axios and the built-in https module), Python, Go, and Rust.

## Rate limits and plan gating

Every request counts against your plan's per-minute and daily quota. Limits and endpoint access are set by your subscription tier and match the plans on the[pricing page](/pricing):

| Plan | API access and rate limit |
| --- | --- |
| Hobby | Market data endpoints, 4 requests/min, 100 per day |
| Individual | Limited endpoints, 10 requests/min, 2,000/day |
| Builder | All endpoints, 100 requests/min, 20,000/day |
| Scale | All endpoints, 500 requests/min, 100,000/day |
| Enterprise | Custom |

The free Hobby tier is limited to market-data endpoints; social, creator, and AI endpoints unlock on paid plans. Enterprise limits are custom — [talk to sales](/enterprise).

![LunarCrush](/logo-horizontal-white.svg)![LunarCrush](/logo-horizontal-dark.svg)

![LunarCrush](/logo-horizontal-white.svg)![LunarCrush](/logo-horizontal-dark.svg)

[Unlock real-time social intelligence.UPGRADE](https://lunarcrush.com/pricing)

[Unlock real-time social intelligence.UPGRADE](https://lunarcrush.com/pricing)

[Unlock real-time social intelligence.UPGRADE](https://lunarcrush.com/pricing)

[Unlock real-time social intelligence.UPGRADE](https://lunarcrush.com/pricing)

[Unlock real-time social intelligence.UPGRADE](https://lunarcrush.com/pricing)
