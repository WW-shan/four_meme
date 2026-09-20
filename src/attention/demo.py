"""Explicitly synthetic, isolated dashboard fixtures; never used for trading."""
import time

from src.attention.store import Store


def seed_demo(path):
    store = Store(path)
    if store.rows() or store.get("mode"):
        raise ValueError("demo requires an empty database")
    now = time.time()
    store.set("mode", "demo")
    store.set("demo_as_of", now + 3)
    store.set("stale_seconds", 180)
    store.set("started", now - 3600)
    assets = [("sol", "So11111111111111111111111111111111111111112", "ORBIT", "Orbit research"),
              ("bsc", "0x" + "1" * 40, "EMBER", "Ember community"),
              ("base", "0x" + "2" * 40, "TIDE", "Tide narrative"),
              ("eth", "0x" + "3" * 40, "GLASS", "Glass network")]
    for i, (chain, addr, symbol, name) in enumerate(assets):
        store.append("gmgn", "token", chain + ":" + addr, {"chain": chain, "address": addr,
                     "name": name, "symbol": symbol, "visiting_count": [820, 410, 92, 0][i],
                     "rank": i + 1, "market_cap": 125000 * (i + 1), "liquidity": 35000 * (i + 1), "price": 0.00012}, now - 20)
        for offset in range(0, 2161, 60):
            store.health("x:topic-" + str(i), "ok", "Synthetic fixture", now - 2160 + offset)
        for j in range(15 - i * 3):
            created = now - (j * 31 if i != 2 else j * 155)
            post = {"id": f"{i+1}{j:04}", "text": f"Demo discussion of {symbol}. Contract {addr}",
                    "created": created, "author_id": str(100 + j), "username": f"demo_author_{j}",
                    "kind": ["original", "quote", "repost", "reply"][j % 4], "is_kol": j % 5 == 0,
                    "topic_id": "topic-" + str(i), "topic_name": name, "metrics": {"retweet_count": j * 4},
                    "references": [], "url": "", "author_followers": 10000}
            store.append("x", "post", post["id"], post, created + 2)
    store.append("x", "post", "999", {"id": "999", "text": "Demo: a new narrative without a verified contract.",
                 "created": now - 30, "author_id": "999", "username": "demo_editor", "kind": "original",
                 "is_kol": True, "topic_id": "unmapped", "topic_name": "An emerging idea", "metrics": {},
                 "references": [], "url": "", "author_followers": None}, now - 28)
    store.health("x:unmapped", "ok", "Synthetic fixture", now)
    store.health("gmgn", "ok", "Synthetic fixture: not live market data", now)
    return store
