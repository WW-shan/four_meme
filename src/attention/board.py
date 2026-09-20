"""Causal views of observed attention; no trading score or authorization."""
import math
import time

from src.attention.identity import mentions

WINDOWS = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "24h": 86400}


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def metrics(posts, now, seconds):
    selected = [p for p in posts if now - seconds < p["created"] <= now]
    authors = {p["author_id"] for p in selected}
    originals = [p for p in selected if p["kind"] == "original"]
    repost_counts = [number(p["metrics"].get("retweet_count")) for p in originals]
    return {"mentions": len(selected), "authors": len(authors),
            **{kind: sum(p["kind"] == kind for p in selected) for kind in ("original", "repost", "quote", "reply")},
            "kol_mentions": sum(p["is_kol"] for p in selected),
            "reported_reposts_of_originals": sum(repost_counts) if all(v is not None for v in repost_counts) else None}


def summarize(posts, now, warm, status="ok", coverage_seconds=0):
    windows = {name: metrics(posts, now, seconds) for name, seconds in WINDOWS.items()}
    recent = windows["15m"]["authors"]
    previous = len({p["author_id"] for p in posts if now - 1800 < p["created"] <= now - 900})
    # Small samples get additive smoothing; this is an attention heuristic only.
    acceleration = round((recent + 1) / (previous + 1), 3) if warm else None
    activity = round(math.log1p(windows["1h"]["authors"]) * 10, 3)
    rising = round(math.log1p(recent) * max(0, math.log2(acceleration)), 3) if acceleration else None
    return {"windows": windows, "activity_score": activity, "rising_score": rising,
            "author_acceleration_15m": acceleration, "baseline_ready": warm,
            "source_status": status, "ranking_eligible": status == "ok",
            "window_complete": {name: coverage_seconds >= seconds for name, seconds in WINDOWS.items()},
            "mentions_5m_bins": [sum(now - (12-i)*300 < p["created"] <= now-(11-i)*300 for p in posts) for i in range(12)],
            "evidence": sorted(posts, key=lambda p: (p["created"], p["id"]), reverse=True)[:30]}


def build_board(store, now=None):
    now = (store.get("demo_as_of", time.time()) if store.get("mode") == "demo" else time.time()) if now is None else float(now)
    rows = store.rows(now - 86400 - 1800, now, kinds=("post", "health"))
    configurations = store.latest("config", now)
    scope = configurations[-1]["payload"] if configurations else None
    stale_seconds = scope["stale_seconds"] if scope else 180
    tokens = {r["entity"]: {**r["payload"], "observed": r["observed"]} for r in store.latest("token", now)}
    health = {r["source"]: r["payload"] for r in store.latest("health", now)}
    posts, health_history = {}, {}
    topic_posts = {t["id"]: {"name": t["name"], "ids": set()} for t in scope["topics"]} if scope else {}
    if scope:
        allowed_sources = {"gmgn"} | ({"x:" + t for t in topic_posts} if topic_posts else {"x"})
        health = {s: p for s, p in health.items() if s in allowed_sources}
        for source in allowed_sources:
            health.setdefault(source, {"source": source, "status": "pending", "attempted": None,
                                       "succeeded": None, "detail": "Awaiting first source check"})
    for row in rows:
        p = row["payload"]
        if row["kind"] == "health":
            health_history.setdefault(row["source"], []).append(row)
        elif row["kind"] == "post":
            if not now - 86400 < p["created"] <= now or row["observed"] < p["created"]:
                continue
            if scope and p["topic_id"] not in topic_posts:
                continue
            old = posts.get(p["id"])
            posts[p["id"]] = {**p, "first_observed": old["first_observed"] if old else row["observed"],
                               "observed": row["observed"],
                               "topic_ids": sorted(set((old or {}).get("topic_ids", []) + [p["topic_id"]]))}
            topic_posts.setdefault(p["topic_id"], {"name": p["topic_name"], "ids": set()})["ids"].add(p["id"])
    for source, state in health.items():
        if state["attempted"] is not None and now - state["attempted"] > stale_seconds:
            state["status"] = "stale"
        state["age_seconds"] = round(now - state["attempted"], 1) if state["attempted"] is not None else None

    continuous = {}
    for source, history in health_history.items():
        previous = now
        if health.get(source, {}).get("status") != "ok":
            continue
        for record in reversed(history):
            if record["payload"]["status"] != "ok" or previous - record["observed"] > stale_seconds:
                break
            previous = record["observed"]
        continuous[source] = now - previous

    associated = {}
    known = list(tokens.values())
    for post in posts.values():
        post["contracts"] = mentions(post["text"], known)
        for match in post["contracts"]:
            if match["status"] == "observed_contract_match":
                key = match["chain"] + ":" + match["address"]
                associated.setdefault(key, []).append(post)
    topic_board = []
    for key, topic in topic_posts.items():
        group = [posts[pid] for pid in topic["ids"]]
        status = health.get("x:" + key, {}).get("status", "unknown")
        coverage = continuous.get("x:" + key, 0)
        summary = summarize(group, now, coverage >= 1800, status, coverage)
        if not group and status != "ok":
            summary["windows"] = {name: {field: None for field in values} for name, values in summary["windows"].items()}
        if status != "ok":
            summary["activity_score"] = None
        topic_board.append({"id": key, "name": topic["name"],
                            "linked_chains": sorted({c["chain"] for p in group for c in p["contracts"]
                                                     if c["status"] == "observed_contract_match"}), **summary})
    token_board = []
    for key, token in tokens.items():
        if token["observed"] < now - 86400 or (scope and token["chain"] not in scope["chains"]):
            continue
        group = associated.get(key, [])
        source_topics = {t for p in group for t in p["topic_ids"]}
        coverage = min((continuous.get("x:" + t, 0) for t in source_topics), default=0)
        social_status = "ok" if source_topics and all(health.get("x:" + t, {}).get("status") == "ok" for t in source_topics) else "incomplete"
        social = summarize(group, now, coverage >= 1800, social_status, coverage) if group else None
        if social and social_status != "ok":
            social["activity_score"] = None
        token_board.append({"id": key, "chain": token["chain"], "address": token["address"],
                            "name": str(token.get("name", "")), "symbol": str(token.get("symbol", "")),
                            "observed": token["observed"], "stale": now - token["observed"] > stale_seconds,
                            "platform_visits_1h": number(token.get("visiting_count")),
                            "provider_rank": number(token.get("rank")),
                            "price_usd": number(token.get("price")), "market_cap_usd": number(token.get("market_cap")),
                            "liquidity_usd": number(token.get("liquidity")),
                            "mapping": "explicit_post_address" if group else "provider_candidate_only",
                            "social": social, "trading_eligible": False})
    chain_order = {chain: index for index, chain in enumerate(("sol", "bsc", "base", "eth"))}
    token_board.sort(key=lambda x: (chain_order[x["chain"]], x["stale"], -(x["platform_visits_1h"] or 0), x["id"]))
    chain_ranks = {}
    for token in token_board:
        if token["stale"] or token["platform_visits_1h"] is None:
            token["chain_rank"] = None
        else:
            chain_ranks[token["chain"]] = chain_ranks.get(token["chain"], 0) + 1
            token["chain_rank"] = chain_ranks[token["chain"]]
    topic_board.sort(key=lambda x: (not x["ranking_eligible"], -(x["activity_score"] or 0), x["id"]))
    kol_events = sorted([p for p in posts.values() if p["is_kol"]], key=lambda p: p["created"], reverse=True)
    return {"schema_version": 1, "as_of": now, "mode": store.get("mode", "live"),
            "trading_enabled": False, "coverage": "Configured X queries and GMGN candidates only",
            "ranking_version": "authors-log-v1", "ranking_description": "Activity = 10*ln(1+unique authors in 1h); rising = ln(1+authors in 15m)*max(0,log2((authors15m+1)/(previous15m+1))). Provider visits are separate.",
            "sources": sorted(health.values(), key=lambda item: item["source"]), "topics": topic_board, "tokens": token_board,
            "kol_events_total": len(kol_events), "kol_events_truncated": len(kol_events) > 100,
            "kol_events": kol_events[:100]}
