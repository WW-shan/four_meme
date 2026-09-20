* [Go to Sanapi](https://api.santiment.net/)
* [Overview](#overview)
* [Example](#example)
* [How to Access the API](#how-to-access-the-api)
* [Fetching Metrics](#fetching-metrics)
* [Metric Versions](#metric-versions)
* [Common GraphQL Queries](#common-graphql-queries)
* [Rate Limits](#rate-limits)
* [Historical and Realtime Data Restrictions](#historical-and-realtime-data-restrictions)
* [Complexity](#complexity)
* [Glossary](#glossary)
* [Supported Blockchains](#supported-blockchains)


# API Reference

* [Overview](#overview)
* [How to Access the API](#how-to-access-the-api)
* [Fetching Metrics](#fetching-metrics)
* [Metric Versions](#metric-versions)
* [Rate Limits](#rate-limits)
* [Complexity](#complexity)
* [Glossary](#glossary)
* [Supported Blockchains](#supported-blockchains)

## Overview

The Santiment API utilizes [GraphQL](https://graphql.org). The decision to use GraphQL over REST was made for several reasons:

* It allows you to request precisely the data you need and conveniently batch requests together. This effectively addresses the issues of underfetching and overfetching data. For instance, why fetch all 20+ fields of a project when you only need its name?
* The request describes the format of the response. This eliminates the need to guess what data the result contains and how to parse it.
* It provides an easy, out-of-the-box method to explore our API via our Live Explorer.

### Example

The example below demonstrates a GraphQL query used to fetch timeseries price data. The parameters control the time range, the interval between data points, and how all the values within an interval are aggregated.

```


1



{



2



getMetric(metric: "twitter_followers") {



3



timeseriesDataJson(



4



slug: "ethereum"



5



from: "utc_now-90d"



6



to: "utc_now-60d"



7



interval: "1d"



8



)



9



}



10



}


```

[Run in Explorer](https://api.santiment.net/graphiql?query=%7B%0A++getMetric%28metric%3A+%22twitter_followers%22%29+%7B%0A++++timeseriesDataJson%28%0A++++++slug%3A+%22ethereum%22%0A++++++from%3A+%22utc_now-90d%22%0A++++++to%3A+%22utc_now-60d%22%0A++++++interval%3A+%221d%22%0A++++%29%0A++%7D%0A%7D)

## How to Access the API

Instructions on how to access the API can be found on the [Accessing the API](/sanapi/accessing-the-api/) page.

## Fetching Metrics

You can find information on how to explore and fetch the available metrics on the [Fetching Metrics](/sanapi/fetching-metrics/) page.

## Metric Versions

Some metrics have multiple implementations exposed through the `version` parameter on `getMetric`. To learn how to discover available versions and query them safely, see [Metric Versions](/sanapi/metric-versions/).

## Common GraphQL Queries

The list of [Common GraphQL queries](/sanapi/common-queries/) gives examples for the different types of queries one can craft and execute.

## Rate Limits

The [Rate Limits Page](/sanapi/rate-limits/) provides detailed information on how API calls are counted and how rate limits are applied.

## Historical and Realtime Data Restrictions

The [Historical and Realtime data restrictions](/sanapi/historical-and-realtime-data-restrictions/) page provides information about the applied restrictions per plan.

## Complexity

Each API query has a limit to the amount of data points it can fetch. The [Complexity Page](/sanapi/complexity/) provides a detailed explanation on how complexity analysis determines whether a given query will be executed or rejected.

## Glossary

You can find the definitions of some terms used on this page in our dedicated [glossary page](/glossary/).

## Supported Blockchains

You can find information about the blockchains we support on our [Supported Blockchains page](/sanapi/supported-blockchains/).

[#### Talk to us in Discord

Still have some questions left? Join our Discord and get help from the Santiment team!](https://santiment.net/discord)
