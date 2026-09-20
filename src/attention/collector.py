"""Bounded polling with durable pagination, daily budgets and source health."""
from datetime import datetime, timezone
import hashlib
import time

from src.attention.providers import ProviderError, gmgn_hot_searches, normalize_x, x_recent


class Collector:
    def __init__(self, store, config):
        config.validate()
        self.store, self.config = store, config
        self.stop = None
        if store.get("mode") == "demo":
            raise ValueError("demo database cannot collect live data; use a separate database")
        registry = store.get("topic_queries", {})
        for topic in config.topics:
            if topic["id"] in registry and registry[topic["id"]] != topic["query"]:
                raise ValueError("query changed for existing topic id; use a new id to keep histories comparable")
            registry[topic["id"]] = topic["query"]
        store.set("topic_queries", registry)
        store.set("mode", "live")
        store.set("topics", config.topics)
        store.set("stale_seconds", config.stale_seconds)
        scope = {"topics": config.topics, "chains": list(config.chains), "kol_accounts": list(config.kol_accounts),
                 "stale_seconds": config.stale_seconds, "poll_seconds": config.poll_seconds,
                 "x_daily_requests": config.x_daily_requests}
        if store.get("scope") != scope:
            store.append("attention", "config", "scope", scope)
            store.set("scope", scope)
        if not store.get("started"):
            store.set("started", time.time())

    def tick(self):
        self._run("gmgn", self._gmgn)
        topics = self.config.topics
        offset = int(self.store.get("topic_rotation", 0)) % max(len(topics), 1)
        for topic in topics[offset:] + topics[:offset]:
            if self.stop is not None and self.stop.is_set():
                break
            self._run("x:" + topic["id"], lambda t=topic: self._x(t))
        self.store.set("topic_rotation", offset + 1)
        if not topics:
            self.store.health("x", "unconfigured", "No topic queries configured")

    def _run(self, source, function):
        retry = max(self.store.get("retry:" + source, 0),
                    self.store.get("retry:x_all", 0) if source.startswith("x:") else 0)
        if retry > time.time():
            return
        try:
            function()
        except ProviderError as exc:
            self.store.set("retry:" + source, max(time.time() + 60, exc.retry_at))
            if source.startswith("x:") and exc.http_status in (401, 403, 429):
                self.store.set("retry:x_all", max(time.time() + 60, exc.retry_at))
            self.store.health(source, "error", str(exc))
        except (ValueError, TypeError, KeyError):
            self.store.set("retry:" + source, time.time() + 60)
            self.store.health(source, "error", "Invalid provider data; cursor not advanced")

    def _gmgn(self):
        if not self.config.gmgn_key:
            self.store.health("gmgn", "unconfigured", "GMGN_API_KEY missing")
            return
        tokens, raw = gmgn_hot_searches(self.config.gmgn_key, self.config.chains)
        observed = time.time()
        batch = [("gmgn", "raw", "hot_searches", raw)]
        for token in tokens:
            fields = ("chain", "address", "name", "symbol", "visiting_count", "rank", "price",
                      "market_cap", "liquidity", "provider_interval", "provider_version")
            batch.append(("gmgn", "token", token["chain"] + ":" + token["address"], {k: token.get(k) for k in fields}))
        self.store.append_batch(batch, observed)
        self.store.health("gmgn", "ok", f"{len(tokens)} candidates; platform heat, not X mentions", observed)

    def _x(self, topic):
        source = "x:" + topic["id"]
        if not self.config.x_token:
            self.store.health(source, "unconfigured", "X_BEARER_TOKEN missing")
            return
        # A changed query must not inherit a different query's since_id/cursor.
        cursor_key = "cursor:" + topic["id"] + ":" + hashlib.sha256(topic["query"].encode()).hexdigest()
        cursor = self.store.get(cursor_key, {})
        day = datetime.now(timezone.utc).date().isoformat()
        for _ in range(3):
            if self.stop is not None and self.stop.is_set():
                return
            if not self.store.reserve_x_request(day, self.config.x_daily_requests):
                self.store.health(source, "budget_exhausted", "Daily X request cap reached; coverage is incomplete")
                return
            if not cursor:
                cursor = {"start_time": time.time() - 3600}
            # Freeze the upper boundary across every page and process restart.
            cursor.setdefault("end_time", time.time() - 10)
            self.store.set(cursor_key, cursor)
            raw = x_recent(self.config.x_token, topic["query"], cursor.get("since_id"),
                           cursor.get("next_token"), cursor.get("start_time"), end_time=cursor["end_time"])
            observed = time.time()
            posts = normalize_x(raw, topic, self.config.kol_accounts)
            self.store.append_batch([("x", "raw", topic["id"], raw)] +
                                    [("x", "post", post["id"], post) for post in posts], observed)
            meta = raw["meta"]
            newest = max([int(x) for x in [cursor.get("head"), cursor.get("since_id"), meta.get("newest_id")]
                          + [post["id"] for post in posts] if x] or [0])
            if meta.get("next_token"):
                cursor = {**cursor, "next_token": meta["next_token"], "head": str(newest) if newest else None}
                self.store.set(cursor_key, cursor)
                self.store.health(source, "catching_up", "Pagination pending; signal coverage incomplete", observed)
            else:
                covered_until = cursor["end_time"]
                cursor = {"since_id": str(newest)} if newest else {"start_time": covered_until}
                self.store.set(cursor_key, cursor)
                status = "ok" if observed - covered_until <= self.config.stale_seconds else "catching_up"
                self.store.health(source, status, "Recent-search polling; not a full X firehose", observed)
                return

    def run(self, stop):
        self.stop = stop
        while not stop.is_set():
            self.tick()
            stop.wait(self.config.poll_seconds)
