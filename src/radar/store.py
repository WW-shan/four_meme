"""Append-only SQLite evidence store for scanner launches and snapshots."""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time

SCHEMA_VERSION = 1


class ScannerStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(
                    seq INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    received_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS observations_kind ON observations(kind, entity, observed_at);
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, kind: str, entity: str, payload: dict, observed_at: float, received_at: float | None = None) -> int:
        observed = float(observed_at)
        received = time.time() if received_at is None else float(received_at)
        if not math.isfinite(observed) or observed <= 0:
            raise ValueError("observed_at must be a positive finite timestamp")
        if not math.isfinite(received) or received <= 0:
            raise ValueError("received_at must be a positive finite timestamp")
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO observations(kind, entity, observed_at, received_at, payload) VALUES (?,?,?,?,?)",
                (kind, entity, observed, received, body),
            )
            return int(cursor.lastrowid)

    # Payload chain first (written by the pipeline), then the ``chain:token`` entity prefix
    # used by the radar collector, so rows written before this column existed still filter.
    CHAIN_EXPR = ("COALESCE(json_extract(payload, '$.chain'), "
                  "CASE WHEN instr(entity, ':') > 0 THEN substr(entity, 1, instr(entity, ':') - 1) END)")

    def rows(self, kind: str | None = None, entity: str | None = None,
             since: float = 0, until: float | None = None, limit: int = 1000,
             chain: str | None = None, platform: str | None = None) -> list[dict]:
        until = time.time() if until is None else float(until)
        clauses, params = ["observed_at >= ?", "observed_at <= ?"], [float(since), until]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if entity is not None:
            clauses.append("entity = ?")
            params.append(entity)
        if chain is not None:
            clauses.append(f"{self.CHAIN_EXPR} = ?")
            params.append(chain)
        if platform is not None:
            clauses.append("json_extract(payload, '$.platform') = ?")
            params.append(platform)
        params.append(int(limit))
        sql = (
            "SELECT * FROM observations WHERE " + " AND ".join(clauses)
            + " ORDER BY observed_at, seq LIMIT ?"
        )
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def chain_activity(self, since: float = 0, until: float | None = None) -> list[dict]:
        """Per-chain observation counts and last-seen time for the dashboard status strip."""
        until = time.time() if until is None else float(until)
        sql = (
            "SELECT " + self.CHAIN_EXPR + " AS chain, COUNT(*) AS observations, "
            "MIN(observed_at) AS first_observed_at, MAX(observed_at) AS last_observed_at "
            "FROM observations WHERE observed_at >= ? AND observed_at <= ? "
            "AND " + self.CHAIN_EXPR + " IS NOT NULL GROUP BY chain ORDER BY chain"
        )
        with self.connect() as db:
            rows = db.execute(sql, [float(since), until]).fetchall()
        return [dict(row) for row in rows]

    def platform_activity(self, chain: str | None = None, since: float = 0,
                          until: float | None = None) -> list[dict]:
        until = time.time() if until is None else float(until)
        clauses = ["observed_at >= ?", "observed_at <= ?", "json_extract(payload, '$.platform') IS NOT NULL"]
        params: list = [float(since), until]
        if chain is not None:
            clauses.append(f"{self.CHAIN_EXPR} = ?")
            params.append(chain)
        sql = (
            "SELECT " + self.CHAIN_EXPR + " AS chain, json_extract(payload, '$.platform') AS platform, "
            "COUNT(*) AS observations, MAX(observed_at) AS last_observed_at "
            "FROM observations WHERE " + " AND ".join(clauses) + " GROUP BY chain, platform ORDER BY observations DESC"
        )
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def latest(self, kind: str, entity: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM observations WHERE kind=? AND entity=? ORDER BY observed_at DESC, seq DESC LIMIT 1",
                (kind, entity),
            ).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None
