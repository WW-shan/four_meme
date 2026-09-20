"""Documented read-only endpoints. Credentials never enter stored payloads."""
from datetime import datetime, timezone
import math
import time
import uuid

import requests

from src.attention.identity import address


class ProviderError(RuntimeError):
    def __init__(self, message, retry_at=0, http_status=None):
        super().__init__(message)
        self.retry_at = retry_at
        self.http_status = http_status


def request_json(session, method, url, **kwargs):
    try:
        response = session.request(method, url, timeout=(5, 25), allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise ProviderError("network request failed", time.time() + 60) from None
    if response.status_code != 200:
        delay = 3600 if response.status_code in (401, 403) else 60
        retry_at = time.time() + delay
        if response.status_code == 429:
            for key in ("x-rate-limit-reset", "x-ratelimit-reset"):
                try:
                    reset = float(response.headers.get(key, 0))
                    if math.isfinite(reset):
                        retry_at = max(retry_at, reset + 1)
                except (TypeError, ValueError):
                    pass
            try:
                reset = float(response.headers.get("Retry-After", 0))
                if math.isfinite(reset):
                    retry_at = max(retry_at, time.time() + reset)
            except (TypeError, ValueError):
                pass
        raise ProviderError(f"HTTP {response.status_code}", retry_at, response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise ProviderError("invalid JSON response", time.time() + 60) from None
    if not isinstance(payload, dict):
        raise ProviderError("unexpected response shape", time.time() + 60)
    return payload


def gmgn_hot_searches(key, chains, session=None):
    payload = request_json(session or requests, "POST", "https://openapi.gmgn.ai/v1/market/hot_searches",
                           params={"timestamp": int(time.time()), "client_id": str(uuid.uuid4())},
                           headers={"X-APIKEY": key, "User-Agent": "meme-attention/1.0"},
                           json={"params": [{"chain": c, "interval": "1h", "limit": 50} for c in chains]})
    if payload.get("code") not in (0, "0") or not isinstance(payload.get("data"), list):
        raise ProviderError("GMGN business error or unexpected envelope", time.time() + 60)
    blocks = payload["data"]
    if (len(blocks) != len(chains) or any(not isinstance(b, dict) for b in blocks)
            or {b.get("chain") for b in blocks} != set(chains)):
        raise ProviderError("GMGN missing requested chain blocks", time.time() + 60)
    tokens = []
    for block in blocks:
        if block.get("interval") != "1h" or not isinstance(block.get("tokens"), list):
            raise ProviderError("GMGN invalid block", time.time() + 60)
        for token in block["tokens"]:
            if not isinstance(token, dict):
                raise ProviderError("GMGN invalid token record", time.time() + 60)
            if token.get("chain", block["chain"]) != block["chain"]:
                raise ProviderError("GMGN chain mismatch", time.time() + 60)
            try:
                normalized = address(block["chain"], token.get("address"))
            except ValueError:
                raise ProviderError("GMGN invalid token address", time.time() + 60) from None
            tokens.append({**token, "address": normalized, "chain": block["chain"],
                           "provider_interval": "1h", "provider_version": block.get("version")})
    return tokens, payload


def x_recent(token, query, since_id=None, next_token=None, start_time=None, session=None, end_time=None):
    params = {"query": query, "max_results": 10, "sort_order": "recency",
              "tweet.fields": "created_at,author_id,public_metrics,referenced_tweets,entities",
              "expansions": "author_id", "user.fields": "username,name,public_metrics"}
    if since_id:
        params["since_id"] = since_id
    elif start_time:
        params["start_time"] = datetime.fromtimestamp(start_time, timezone.utc).isoformat().replace("+00:00", "Z")
    if next_token:
        params["next_token"] = next_token
    if end_time:
        params["end_time"] = datetime.fromtimestamp(end_time, timezone.utc).isoformat().replace("+00:00", "Z")
    payload = request_json(session or requests, "GET", "https://api.x.com/2/tweets/search/recent",
                           headers={"Authorization": f"Bearer {token}"}, params=params)
    if payload.get("errors") or not isinstance(payload.get("meta"), dict) or not isinstance(payload.get("data", []), list):
        raise ProviderError("X partial error or unexpected envelope", time.time() + 60)
    meta = payload["meta"]
    if ("result_count" in meta and (type(meta["result_count"]) is not int or meta["result_count"] != len(payload.get("data", [])))):
        raise ProviderError("X result_count mismatch", time.time() + 60)
    for key in ("newest_id", "oldest_id"):
        if key in meta and (not isinstance(meta[key], str) or not meta[key].isdigit()):
            raise ProviderError("X invalid page identity", time.time() + 60)
    if "next_token" in meta and (not isinstance(meta["next_token"], str) or not meta["next_token"]):
        raise ProviderError("X invalid pagination token", time.time() + 60)
    return payload


def normalize_x(payload, topic, kol_accounts):
    includes = payload.get("includes", {})
    if not isinstance(includes, dict) or not isinstance(includes.get("users", []), list):
        raise ProviderError("X invalid author expansion", time.time() + 60)
    authors = {}
    for user in includes.get("users", []):
        if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not isinstance(user.get("username", ""), str):
            raise ProviderError("X invalid author", time.time() + 60)
        authors[user["id"]] = user
    posts = []
    for tweet in payload.get("data", []):
        if not isinstance(tweet, dict) or not isinstance(tweet.get("text", ""), str):
            raise ProviderError("X invalid post record", time.time() + 60)
        author = authors.get(tweet.get("author_id"), {})
        username = author.get("username", "")
        refs = tweet.get("referenced_tweets", [])
        if (not isinstance(refs, list) or any(not isinstance(r, dict) for r in refs)
                or not isinstance(tweet.get("public_metrics", {}), dict)
                or not isinstance(author.get("public_metrics", {}), dict)):
            raise ProviderError("X invalid post metrics or references", time.time() + 60)
        types = {r.get("type") for r in refs}
        kind = "repost" if "retweeted" in types else "quote" if "quoted" in types else "reply" if "replied_to" in types else "original"
        try:
            if not isinstance(tweet.get("created_at"), str):
                raise ValueError()
            timestamp = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError()
            created = timestamp.timestamp()
            if not str(tweet["id"]).isdigit() or not str(tweet["author_id"]).isdigit():
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ProviderError("X invalid post identity or timestamp", time.time() + 60) from None
        posts.append({"id": str(tweet["id"]), "text": tweet.get("text", ""), "created": created,
                      "author_id": tweet["author_id"], "username": username, "kind": kind,
                      "is_kol": username.lower() in kol_accounts, "topic_id": topic["id"],
                      "topic_name": topic["name"], "metrics": tweet.get("public_metrics", {}),
                      "references": refs, "url": f"https://x.com/i/web/status/{tweet['id']}",
                      "author_followers": author.get("public_metrics", {}).get("followers_count")})
    return posts
