> ## Documentation Index
>
> Fetch the complete documentation index at:</llms.txt>
>
> Use this file to discover all available pages before exploring further.

[X home page![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)](/)

* [Support](/support)
* [Developer Console](https://developer.x.com/en/portal/petition/essential/basic-info)

[X home page![light logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/light.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=b2e6f2ff399e1bc5cd0c588f9644983d)![dark logo](https://mintcdn.com/x-preview/CX6FNhUUR8mtNZ97/logo/dark.svg?fit=max&auto=format&n=CX6FNhUUR8mtNZ97&q=85&s=d7b8656c2bcee6e9d5c947f6419e8b82)](/)

Powerstream

# Powerstream low-latency streaming API on the X API v2

Powerstream is the X API v2 lowest-latency streaming endpoint for public Post data, using PowerTrack-style rules to filter by keywords, operators, and metadata.

Powerstream is our **lowest-latency streaming API** for accessing public X data in real-time. Unlike other streaming endpoints that prioritize data hydration and delivery (with ~6-7 seconds P99 latency), Powerstream is optimized for speed and delivers data with minimal delay. Similar to the legacy GNIP Powertrack API, it uses rules to filter Posts based on keywords, operators, and metadata. Once a persistent HTTP connection is made to the Powerstream endpoint, you can start receiving matching Posts in real-time. Currently, Powerstream supports up to 1,000 rules and each rule can be 2048 characters.

## [​](#key-features) Key features

* **Real-time data delivery**: Lowest latency option for streaming Posts as they’re published.
* **Precise filtering**: Filter for exactly the data you are looking for using Boolean queries with operators.
* **Delivery**: JSON response over HTTP/1.1 chunked transfer encoding.
* **Local datacenter support**: Fetch Posts only from the local datacenter to further reduce latency by avoiding replication lag.

The Powerstream API is a premium offering available under select Enterprise plans.If you’re interested in accessing Powerstream or learning more about our Enterprise offerings, please reach out to our Sales team by submitting the [Enterprise Request Form](/forms/enterprise-api-interest). We’ll be happy to discuss how Powerstream can support your needs.

## [​](#quick-start) Quick start

This section showcases how to quickly get started with the PowerStream endpoints using Python with the `requests` library. Install it via `pip install requests`. All examples use OAuth 2.0 Bearer Token authentication. Replace `YOUR_BEARER_TOKEN` with your actual token (store it securely, e.g., via `os.getenv('BEARER_TOKEN')`). We’ll cover each endpoint with code snippets. Assume these imports at the top:

```
import requests import  requests import json import  json import time import  time import sys import  sys import os # For env vars import  os # For env vars
```

### [​](#setup) Setup

```
bearer_token = os.getenv('BEARER_TOKEN') or "YOUR_BEARER_TOKEN" # Use env var for security bearer_token = os.getenv('BEARER_TOKEN') or  "YOUR_BEARER_TOKEN"  # Use env var for securitybase_url = "https://api.x.com/2/powerstream" base_url = "https://api.x.com/2/powerstream"rules_url = f"{base_url}/rules" # For rule management rules_url =  f "{base_url}/rules"  # For rule managementheaders = {headers = { "Authorization": f"Bearer {bearer_token}",  "Authorization": f "Bearer {bearer_token} ", "Content-Type": "application/json" "Content-Type": "application/json"}}
```

### [​](#1-create-rules-post-/rules) 1. Create rules (POST /rules)

Add rules to filter your stream.

Example

```
data = {data = { "rules": [ "rules": [ { { "value": "(cat OR dog) lang:en -is:retweet",  "value": "(cat OR dog) lang:en -is:retweet", "tag": "pet-monitor"  "tag": "pet-monitor" }, }, # Add more rules as needed (up to 100) # Add more rules as needed (up to 100) ] ]}} response = requests.post(rules_url, headers=headers, json=data) response = requests.post(rules_url, headers =headers, json =data)if response.status_code == 201: if response.status_code ==  201: rules_added = response.json().get("data", {}).get("rules", [])  rules_added = response.json().get("data", {}).get("rules", []) print("Rules added:")  print("Rules added:") for rule in rules_added:  for  rule in rules_added: print(f"ID: {rule['id']}, Value: {rule['value']}, Tag: {rule.get('tag', 'N/A')}")  print(f"ID: {rule['id']}, Value: {rule['value']}, Tag: {rule.get('tag', 'N/A')} ")else: else: print(f"Error {response.status_code}: {response.text}")  print(f "Error {response.status_code}: {response.text} ")
```

### [​](#2-delete-rules-post-/rules) 2. Delete rules (POST /rules)

Remove rules by ID (recommended) or value.

Example

```
data = {data = { "rules": [ "rules": [ { { "value": "(cat OR dog) lang:en -is:retweet",  "value": "(cat OR dog) lang:en -is:retweet", "tag": "pet-monitor"  "tag": "pet-monitor" }, }, # Add more rules as needed (up to 100) # Add more rules as needed (up to 100) ] ]}} response = requests.delete(rules_url, headers=headers, json=data) response = requests.delete(rules_url, headers =headers, json =data)if response.status_code == 200: if response.status_code ==  200: deleted = response.json().get("data", {})  deleted = response.json().get("data", {}) print(f"Deleted count: {deleted.get('deleted', 'N/A')}")  print(f"Deleted count: {deleted.get('deleted', 'N/A')} ") if 'not_deleted' in deleted:  if  'not_deleted'  in deleted: print("Not deleted:", deleted['not_deleted'])  print("Not deleted:", deleted['not_deleted'])else: else: print(f"Error {response.status_code}: {response.text}")  print(f "Error {response.status_code}: {response.text} ")
```

**Tip**: To delete all rules, first GET them, extract IDs, then delete in bulk.

### [​](#3-get-rules-get-/rules) 3. Get rules (GET /rules)

Fetch all active rules.

```
response = requests.get(rules_url, headers=headers) response = requests.get(rules_url, headers =headers)if response.status_code == 200: if response.status_code ==  200: rules = response.json().get("data", {}).get("rules", [])  rules = response.json().get("data", {}).get("rules", []) if rules:  if rules: print("Active rules:")  print("Active rules:") for rule in rules:  for  rule in rules: print(f"ID: {rule['id']}, Value: {rule['value']}, Tag: {rule.get('tag', 'N/A')}")  print(f"ID: {rule['id']}, Value: {rule['value']}, Tag: {rule.get('tag', 'N/A')} ") else:  else: print("No active rules.")  print("No active rules.")else: else: print(f"Error {response.status_code}: {response.text}")  print(f "Error {response.status_code}: {response.text} ")
```

### [​](#4-powerstream-get-/stream) 4. PowerStream (GET /stream)

Connect to the stream for real-time, low-latency Posts. Use `stream=True` for line-by-line reading. Implement reconnect logic for robustness.

Example

```
stream_url = base_url stream_url =  base_url def main(): def  main(): while True:  while  True: response = requests.request("GET", stream_url, headers=headers, stream=True)  response = requests.request("GET", stream_url, headers =headers, stream = True) print(response.status_code)  print(response.status_code) for response_line in response.iter_lines():  for  response_line in response.iter_lines(): if response_line:  if response_line: json_response = json.loads(response_line)  json_response = json.loads(response_line) print(json.dumps(json_response, indent=4, sort_keys=True))  print(json.dumps(json_response, indent = 4, sort_keys = True)) if response.status_code != 200:  if response.status_code !=  200: print(response.headers)  print(response.headers) raise Exception( raise  Exception( "Request returned an error: {} {}".format( "Request returned an error: {} {} ".format( response.status_code, response.text response.status_code, response.text ) ) ) )
```

#### [​](#local-datacenter-support) Local datacenter support

For latency optimization, Powerstream provides an option to fetch only posts that originated or were created in the local datacenter where a connection is established. This avoids replication lag, resulting in faster delivery compared to posts from other datacenters. To enable this, append the query parameter `?localDcOnly=true` to the stream endpoint (e.g., `/2/powerstream?localDcOnly=true`). The datacenter you are connected to will be indicated both in the initial data payload of the stream and as an HTTP header in the response. To use in code:

```
# For local datacenter only:# For local datacenter only:stream_url = "https://api.x.com/2/powerstream?localDcOnly=true" stream_url = "https://api.x.com/2/powerstream?localDcOnly=true"
```

If the `localDcOnly` parameter is enabled, when the stream first connects, it will include the following response headers indicating which local datacenter is being used:

```
'x-powerstream-datacenter': 'atla','x-powerstream-datacenter': 'atla','x-powerstream-localdconly': 'true''x-powerstream-localdconly':  'true'
```

In addition to this, it will also send an initial payload specifying the datacenter:

```
{{ "type": "connection_metadata",  "type": "connection_metadata", "datacenter": "atla",  "datacenter": "atla", "timestamp": 1762557264155  "timestamp":  1762557264155}}
```

**Tip:** To optimize latency, set up connections from different geographic locations (e.g., one near Atlanta on the US East Coast and another near Portland on the US West Coast), enabling `localDcOnly=true` for each. This provides faster access to posts from each respective datacenter. Aggregate the streams on your end to combine cross-datacenter data.

## [​](#operators) Operators

In order to set rules for filtering, you can use keywords and operators.

## Powerstream operators

Complete list of available operators

---

## [​](#responses) Responses

The payload of the Powerstream API is the same format as the legacy GNIP Powertrack API. A sample json response looks like:

![](https://mintcdn.com/x-preview/Vn2KEkZaPF9LiPi3/icons/xds/icon-brackets.svg?fit=max&auto=format&n=Vn2KEkZaPF9LiPi3&q=85&s=ed2428e77bab43e57800e1a590e982fa)Example response

```
[[ { { "created_at": "Tue Mar 21 20:50:14 +0000 2006",  "created_at": "Tue Mar 21 20:50:14 +0000 2006", "id": 20,  "id": 20, "id_str": "20",  "id_str": "20", "text": "just setting up my twttr",  "text": "just setting up my twttr", "truncated": false,  "truncated": false, "entities": { "entities": { "hashtags": [],  "hashtags": [], "symbols": [],  "symbols": [], "user_mentions": [],  "user_mentions": [], "urls": []  "urls": [] }, }, "source": "\"http://x.com\" rel=\"nofollow\">X Web Client",  "source": "\"http://x.com\" rel=\"nofollow\">X Web Client" \"http://x.com \" rel= \" nofollow \">X Web Client, "in_reply_to_status_id": null,  "in_reply_to_status_id": null, "in_reply_to_status_id_str": null,  "in_reply_to_status_id_str": null, "in_reply_to_user_id": null,  "in_reply_to_user_id": null, "in_reply_to_user_id_str": null,  "in_reply_to_user_id_str": null, "in_reply_to_screen_name": null,  "in_reply_to_screen_name": null, "user": { "user": { "id": 12,  "id": 12, "id_str": "12",  "id_str": "12", "name": "jack",  "name": "jack", "screen_name": "jack",  "screen_name": "jack", "location": "",  "location": "", "description": "no state is the best state",  "description": "no state is the best state", "url": "https://t.co/ZEpOg6rn5L",  "url": "https://t.co/ZEpOg6rn5L", "entities": { "entities": { "url": { "url": { "urls": [ "urls": [ { { "url": "https://t.co/ZEpOg6rn5L",  "url": "https://t.co/ZEpOg6rn5L", "expanded_url": "http://primal.net/jack",  "expanded_url": "http://primal.net/jack", "display_url": "primal.net/jack",  "display_url": "primal.net/jack", "indices": [ "indices": [ 0,  0,  23  23 ] ] } } ] ] }, }, "description": { "description": { "urls": []  "urls": [] } } }, }, "protected": false,  "protected": false, "followers_count": 6427829,  "followers_count": 6427829, "friends_count": 3,  "friends_count": 3, "listed_count": 32968,  "listed_count": 32968, "created_at": "Tue Mar 21 20:50:14 +0000 2006",  "created_at": "Tue Mar 21 20:50:14 +0000 2006", "favourites_count": 36306,  "favourites_count": 36306, "utc_offset": null,  "utc_offset": null, "time_zone": null,  "time_zone": null, "geo_enabled": true,  "geo_enabled": true, "verified": false,  "verified": false, "statuses_count": 30134,  "statuses_count": 30134, "lang": null,  "lang": null, "contributors_enabled": false,  "contributors_enabled": false, "is_translator": false,  "is_translator": false, "is_translation_enabled": false,  "is_translation_enabled": false, "profile_background_color": "EBEBEB",  "profile_background_color": "EBEBEB", "profile_background_image_url": "http://abs.twimg.com/images/themes/theme7/bg.gif",  "profile_background_image_url": "http://abs.twimg.com/images/themes/theme7/bg.gif", "profile_background_image_url_https": "https://abs.twimg.com/images/themes/theme7/bg.gif",  "profile_background_image_url_https": "https://abs.twimg.com/images/themes/theme7/bg.gif", "profile_background_tile": false,  "profile_background_tile": false, "profile_image_url": "http://pbs.twimg.com/profile_images/1661201415899951105/azNjKOSH_normal.jpg",  "profile_image_url": "http://pbs.twimg.com/profile_images/1661201415899951105/azNjKOSH_normal.jpg", "profile_image_url_https": "https://pbs.twimg.com/profile_images/1661201415899951105/azNjKOSH_normal.jpg",  "profile_image_url_https": "https://pbs.twimg.com/profile_images/1661201415899951105/azNjKOSH_normal.jpg", "profile_banner_url": "https://pbs.twimg.com/profile_banners/12/1742427520",  "profile_banner_url": "https://pbs.twimg.com/profile_banners/12/1742427520", "profile_link_color": "990000",  "profile_link_color": "990000", "profile_sidebar_border_color": "DFDFDF",  "profile_sidebar_border_color": "DFDFDF", "profile_sidebar_fill_color": "F3F3F3",  "profile_sidebar_fill_color": "F3F3F3", "profile_text_color": "333333",  "profile_text_color": "333333", "profile_use_background_image": true,  "profile_use_background_image": true, "has_extended_profile": true,  "has_extended_profile": true, "default_profile": false,  "default_profile": false, "default_profile_image": false,  "default_profile_image": false, "following": null,  "following": null, "follow_request_sent": null,  "follow_request_sent": null, "notifications": null,  "notifications": null, "translator_type": "regular",  "translator_type": "regular", "withheld_in_countries": []  "withheld_in_countries": [] }, }, "geo": null,  "geo": null, "coordinates": null,  "coordinates": null, "place": null,  "place": null, "contributors": null,  "contributors": null, "is_quote_status": false,  "is_quote_status": false, "retweet_count": 122086,  "retweet_count": 122086, "favorite_count": 263321,  "favorite_count": 263321, "favorited": false,  "favorited": false, "retweeted": false,  "retweeted": false, "lang": "en"  "lang": "en" } }]]
```

## [​](#limits-and-best-practices) Limits and best practices

* Rate Limits: 50 requests/24h for rule management; no limit on streams (but connection limits apply).
* Reconnection: Exponential backoff on disconnects.
* Monitoring: Use `Connection: keep-alive` headers.

---

## [​](#streaming-fundamentals) Streaming fundamentals

## Consuming streaming data

Best practices for streaming clients

## Handling disconnections

Reconnect gracefully

## High volume capacity

Handle high throughput

![](https://mintcdn.com/x-preview/cfyQtgCdwk8p69aa/icons/xds/icon-shield-keyhole.svg?fit=max&auto=format&n=cfyQtgCdwk8p69aa&q=85&s=a0e05514090c8a6af232297bfb9c4055)

## Recovery and redundancy

Build resilient applications
