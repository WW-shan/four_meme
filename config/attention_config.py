"""Configuration for the independent, read-only attention service."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re

from dotenv import dotenv_values


@dataclass
class AttentionConfig:
    chains: tuple = ("sol", "bsc", "base", "eth")
    poll_seconds: int = 60
    stale_seconds: int = 180
    x_daily_requests: int = 200
    gmgn_key: str = field(default="", repr=False)
    x_token: str = field(default="", repr=False)
    topics: list = field(default_factory=list)
    kol_accounts: tuple = ()

    def validate(self):
        if not self.chains or set(self.chains) - {"sol", "bsc", "base", "eth"} or len(set(self.chains)) != len(self.chains):
            raise ValueError("choose unique supported chains: sol, bsc, base, eth")
        if any(type(v) is not int for v in (self.poll_seconds, self.stale_seconds, self.x_daily_requests)):
            raise ValueError("poll_seconds, stale_seconds and x_daily_requests must be integers")
        if self.poll_seconds < 30 or self.stale_seconds < self.poll_seconds * 2:
            raise ValueError("poll_seconds >= 30 and stale_seconds >= 2 * poll_seconds required")
        if self.x_daily_requests < 0:
            raise ValueError("x_daily_requests must be non-negative")
        if not isinstance(self.topics, list) or len(self.topics) > 100:
            raise ValueError("topics must be a list of at most 100 queries")
        if any(not re.fullmatch(r"[a-z0-9_]{1,15}", value) for value in self.kol_accounts):
            raise ValueError("kol_accounts must contain X handles")
        seen = set()
        for topic in self.topics:
            if not isinstance(topic, dict) or not all(isinstance(topic.get(k), str) and topic[k].strip() for k in ("id", "name", "query")):
                raise ValueError("each topic needs id, name and query")
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", topic["id"]):
                raise ValueError("topic id must use letters, digits, underscore or dash")
            if topic["id"] in seen or len(topic["query"]) > 512:
                raise ValueError("topic ids must be unique; queries must be <= 512 characters")
            seen.add(topic["id"])
        return self

    @classmethod
    def load(cls, sources=None):
        root = Path(__file__).resolve().parents[1]
        values = {**dotenv_values(Path.home() / ".config/gmgn/.env"),
                  **dotenv_values(root / ".env"), **os.environ}
        config = cls(
            gmgn_key=values.get("GMGN_API_KEY", "") or "",
            x_token=values.get("X_BEARER_TOKEN", "") or "",
        )
        if sources:
            data = json.loads(Path(sources).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("source configuration must be a JSON object")
            unknown = set(data) - {"topics", "kol_accounts", "chains", "poll_seconds", "stale_seconds", "x_daily_requests"}
            if unknown:
                raise ValueError("unknown source configuration fields: " + ", ".join(sorted(unknown)))
            if any(not isinstance(data[k], list) for k in ("topics", "kol_accounts", "chains") if k in data):
                raise ValueError("topics, kol_accounts and chains must be arrays")
            config.topics = data.get("topics", [])
            config.kol_accounts = tuple(str(x).lower().lstrip("@") for x in data.get("kol_accounts", []))
            config.chains = tuple(data.get("chains", config.chains))
            config.poll_seconds = data.get("poll_seconds", config.poll_seconds)
            config.stale_seconds = data.get("stale_seconds", config.stale_seconds)
            config.x_daily_requests = data.get("x_daily_requests", config.x_daily_requests)
        return config.validate()
