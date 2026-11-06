"""The motor's only state: consumers, provider secrets, provider health.

One SQLite file (`<data>/motor.db`). No copy of provider data lives here —
the motor is a plain proxy (docs/motor.md, «Как устроен»).

- consumers: who may call the motor; only a SHA-256 of the token is kept,
  the token itself is shown once when it is issued.
- secrets: external tokens entered on the page, per provider and field.
  Never returned by any endpoint; the page only says whether one is set.
- provider_state: «работает / протух / лежит» and when it last answered.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

TOKEN_PREFIX = "motor_"
# Bookkeeping writes (last seen, last ok) at most this often per row.
TOUCH_EVERY = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consumers (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   REAL NOT NULL,
    last_seen_at REAL
);
CREATE TABLE IF NOT EXISTS secrets (
    provider   TEXT NOT NULL,
    field      TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (provider, field)
);
CREATE TABLE IF NOT EXISTS provider_state (
    provider   TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    message    TEXT NOT NULL DEFAULT '',
    last_ok_at REAL,
    changed_at REAL NOT NULL
);
"""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Consumer:
    id: int
    name: str
    created_at: float
    last_seen_at: float | None


@dataclass(frozen=True)
class ProviderState:
    status: str  # ok | expired | down | error
    message: str
    last_ok_at: float | None
    changed_at: float


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()
        path.chmod(0o600)

    def close(self) -> None:
        self._db.close()

    def _run(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    # ── consumers ────────────────────────────────────────────────────────────

    def add_consumer(self, name: str) -> str:
        """Issue a token for a new consumer; the caller shows it once."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        self._run(
            "INSERT INTO consumers (name, token_hash, created_at) VALUES (?, ?, ?)",
            (name, hash_token(token), time.time()),
        )
        return token

    def consumers(self) -> list[Consumer]:
        rows = self._run("SELECT id, name, created_at, last_seen_at FROM consumers ORDER BY name")
        return [Consumer(**dict(row)) for row in rows]

    def revoke(self, consumer_id: int) -> None:
        self._run("DELETE FROM consumers WHERE id = ?", (consumer_id,))

    def consumer_by_token(self, token: str) -> Consumer | None:
        rows = self._run(
            "SELECT id, name, created_at, last_seen_at FROM consumers WHERE token_hash = ?",
            (hash_token(token),),
        )
        if not rows:
            return None
        consumer = Consumer(**dict(rows[0]))
        now = time.time()
        if consumer.last_seen_at is None or now - consumer.last_seen_at >= TOUCH_EVERY:
            self._run("UPDATE consumers SET last_seen_at = ? WHERE id = ?", (now, consumer.id))
        return consumer

    # ── provider secrets ─────────────────────────────────────────────────────

    def set_secret(self, provider: str, field: str, value: str) -> None:
        self._run(
            "INSERT INTO secrets (provider, field, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (provider, field) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (provider, field, value, time.time()),
        )

    def secret(self, provider: str, field: str) -> str | None:
        rows = self._run("SELECT value FROM secrets WHERE provider = ? AND field = ?", (provider, field))
        return rows[0]["value"] if rows else None

    def secrets_set(self, provider: str) -> dict[str, float]:
        """field → when it was last changed; values never leave the store this way."""
        rows = self._run("SELECT field, updated_at FROM secrets WHERE provider = ?", (provider,))
        return {row["field"]: row["updated_at"] for row in rows}

    # ── provider state ───────────────────────────────────────────────────────

    def provider_state(self, provider: str) -> ProviderState | None:
        rows = self._run(
            "SELECT status, message, last_ok_at, changed_at FROM provider_state WHERE provider = ?",
            (provider,),
        )
        return ProviderState(**dict(rows[0])) if rows else None

    def record(self, provider: str, status: str, message: str = "") -> None:
        """Remember how the provider answered; repeated «ok» is written at most once a minute."""
        now = time.time()
        old = self.provider_state(provider)
        if old and old.status == status == "ok" and old.last_ok_at and now - old.last_ok_at < TOUCH_EVERY:
            return
        if old and old.status == status and old.message == message and status != "ok":
            return
        last_ok = now if status == "ok" else (old.last_ok_at if old else None)
        changed = old.changed_at if old and old.status == status else now
        self._run(
            "INSERT INTO provider_state (provider, status, message, last_ok_at, changed_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (provider) DO UPDATE SET status = excluded.status, message = excluded.message, "
            "last_ok_at = excluded.last_ok_at, changed_at = excluded.changed_at",
            (provider, status, message, last_ok, changed),
        )
