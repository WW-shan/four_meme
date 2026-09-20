"""Append-only source observations and persistent collector cursors."""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(
                    seq INTEGER PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
                    entity TEXT NOT NULL, observed REAL NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS observations_time ON observations(observed);
                CREATE INDEX IF NOT EXISTS observations_entity ON observations(source,kind,entity,observed);
                CREATE INDEX IF NOT EXISTS observations_latest ON observations(kind,source,entity,observed DESC,seq DESC);
                CREATE TABLE IF NOT EXISTS entities(
                    kind TEXT NOT NULL, source TEXT NOT NULL, entity TEXT NOT NULL,
                    PRIMARY KEY(kind,source,entity));
                CREATE TABLE IF NOT EXISTS health(
                    source TEXT PRIMARY KEY, status TEXT NOT NULL, attempted REAL,
                    succeeded REAL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS request_budget(day TEXT PRIMARY KEY, count INTEGER NOT NULL);
            """)
            if not db.execute("SELECT 1 FROM meta WHERE key='entities_index_v1'").fetchone():
                db.execute("INSERT OR IGNORE INTO entities SELECT DISTINCT kind,source,entity FROM observations")
                db.execute("INSERT INTO meta VALUES ('entities_index_v1','true')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def append(self, source, kind, entity, payload, observed=None):
        stamp = time.time() if observed is None else float(observed)
        self.append_batch([(source, kind, entity, payload)], stamp)

    def append_batch(self, records, observed):
        """Publish an entire provider page atomically; readers never see half a page."""
        stamp = float(observed)
        if not math.isfinite(stamp) or stamp <= 0:
            raise ValueError("observed must be a positive finite timestamp")
        rows = [(source, kind, entity, stamp, json.dumps(payload, ensure_ascii=False, allow_nan=False))
                for source, kind, entity, payload in records]
        with self.connect() as db:
            db.executemany("INSERT INTO observations(source,kind,entity,observed,payload) VALUES (?,?,?,?,?)", rows)
            db.executemany("INSERT OR IGNORE INTO entities VALUES (?,?,?)", [(r[1], r[0], r[2]) for r in rows])

    def latest(self, kind, until):
        """Indexed as-of lookup, without loading every token snapshot for the day."""
        with self.connect() as db:
            rows = db.execute("""
                SELECT o.* FROM entities e JOIN observations o ON o.seq=(
                    SELECT seq FROM observations
                    WHERE kind=e.kind AND source=e.source AND entity=e.entity AND observed<=?
                    ORDER BY observed DESC,seq DESC LIMIT 1)
                WHERE e.kind=? ORDER BY o.observed,o.seq
            """, (until, kind)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def rows(self, since=0, until=None, kinds=None):
        until = time.time() if until is None else until
        clause = " AND kind IN (" + ",".join("?" for _ in kinds) + ")" if kinds else ""
        with self.connect() as db:
            rows = db.execute("SELECT * FROM observations WHERE observed>=? AND observed<=?" + clause + " ORDER BY observed,seq",
                              (since, until, *(kinds or []))).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def health(self, source, status, detail="", now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            old = db.execute("""SELECT payload FROM observations WHERE kind='health' AND source=?
                                AND entity=? AND observed<=? ORDER BY observed DESC,seq DESC LIMIT 1""",
                             (source, source, now)).fetchone()
            succeeded = now if status == "ok" else (json.loads(old[0])["succeeded"] if old else None)
            db.execute("""INSERT INTO health VALUES (?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET
                          status=excluded.status,attempted=excluded.attempted,succeeded=excluded.succeeded,
                          detail=excluded.detail WHERE excluded.attempted>=health.attempted""",
                       (source, status, now, succeeded, detail))
            db.execute("INSERT INTO observations(source,kind,entity,observed,payload) VALUES (?,?,?,?,?)",
                       (source, "health", source, now, json.dumps({"source": source, "status": status,
                        "attempted": now, "succeeded": succeeded, "detail": detail})))
            db.execute("INSERT OR IGNORE INTO entities VALUES ('health',?,?)", (source, source))

    def health_rows(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM health ORDER BY source")]

    def reserve_x_request(self, day, limit):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO request_budget VALUES (?,0)", (day,))
            count = db.execute("SELECT count FROM request_budget WHERE day=?", (day,)).fetchone()[0]
            if count >= limit:
                return False
            db.execute("UPDATE request_budget SET count=count+1 WHERE day=?", (day,))
        return True

    def events(self, after=0, limit=100):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM observations WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
