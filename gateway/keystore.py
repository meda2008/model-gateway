"""API Key 签发、校验与限流。

- key 形如 sk-gw-xxxxxxxx，仅创建时明文展示一次，库内只存 sha256
- 限流为整分窗口：当前自然分钟 RPM + 当日请求数（跨分钟边界会重置计数，
  边界处最多可放过 2 倍 burst——对保护免费额度池足够），SQLite 持久化，重启不丢
- request_log 记录每次调用的后端/模型/token/耗时，便于对账
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    prefix TEXT NOT NULL,
    rpm INTEGER NOT NULL,
    daily_requests INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    key_hash TEXT NOT NULL,
    window TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key_hash, window)
);
CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    key_name TEXT NOT NULL,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER
);
"""


@dataclass
class ApiKey:
    key_hash: str
    name: str
    prefix: str
    rpm: int
    daily_requests: int
    enabled: bool


class KeyStore:
    def __init__(self, db_path: str):
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    @staticmethod
    def hash_key(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ---------- key 生命周期 ----------

    def create(self, name: str, rpm: int, daily_requests: int) -> str:
        raw = "sk-gw-" + secrets.token_urlsafe(24)
        with self._lock:
            self._db.execute(
                "INSERT INTO api_keys(key_hash, name, prefix, rpm, daily_requests, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (
                    self.hash_key(raw),
                    name,
                    raw[:10],
                    rpm,
                    daily_requests,
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                ),
            )
            self._db.commit()
        return raw

    def lookup(self, raw: str) -> ApiKey | None:
        with self._lock:
            row = self._db.execute(
                "SELECT key_hash, name, prefix, rpm, daily_requests, enabled"
                " FROM api_keys WHERE key_hash = ?",
                (self.hash_key(raw),),
            ).fetchone()
        if not row:
            return None
        return ApiKey(row[0], row[1], row[2], row[3], row[4], bool(row[5]))

    def list_keys(self) -> list[ApiKey]:
        with self._lock:
            rows = self._db.execute(
                "SELECT key_hash, name, prefix, rpm, daily_requests, enabled FROM api_keys ORDER BY created_at"
            ).fetchall()
        return [ApiKey(r[0], r[1], r[2], r[3], r[4], bool(r[5])) for r in rows]

    def revoke(self, name: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE api_keys SET enabled = 0 WHERE name = ?", (name,))
            self._db.commit()
            return cur.rowcount > 0

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM api_keys WHERE enabled = 1").fetchone()[0]

    # ---------- 限流 ----------

    def check_and_consume(self, key: ApiKey) -> tuple[bool, int, str]:
        """放行则计数；返回 (allowed, retry_after_seconds, reason)。"""
        now = time.time()
        minute_window = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(now))
        day_window = time.strftime("%Y-%m-%d", time.gmtime(now))
        with self._lock:
            for window in (minute_window, day_window):
                self._db.execute(
                    "INSERT INTO usage(key_hash, window, n) VALUES (?,?,1)"
                    " ON CONFLICT(key_hash, window) DO UPDATE SET n = n + 1",
                    (key.key_hash, window),
                )
            self._db.commit()
            minute_n = self._db.execute(
                "SELECT n FROM usage WHERE key_hash=? AND window=?", (key.key_hash, minute_window)
            ).fetchone()[0]
            day_n = self._db.execute(
                "SELECT n FROM usage WHERE key_hash=? AND window=?", (key.key_hash, day_window)
            ).fetchone()[0]
        if minute_n > key.rpm:
            return False, 60 - int(now % 60), f"rpm limit {key.rpm} exceeded"
        if day_n > key.daily_requests:
            return False, 3600, f"daily limit {key.daily_requests} exceeded"
        return True, 0, ""

    # ---------- 用量对账 ----------

    def log_request(
        self,
        key_name: str,
        backend: str,
        model: str,
        usage: dict | None,
        latency_ms: int,
    ) -> None:
        usage = usage or {}
        with self._lock:
            self._db.execute(
                "INSERT INTO request_log(ts, key_name, backend, model, prompt_tokens,"
                " completion_tokens, total_tokens, latency_ms) VALUES (?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    key_name,
                    backend,
                    model,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                    latency_ms,
                ),
            )
            self._db.commit()

    def usage_summary(self, days: int = 7) -> list[tuple]:
        since = time.time() - days * 86400
        with self._lock:
            return self._db.execute(
                "SELECT key_name, backend, model, COUNT(*),"
                " SUM(COALESCE(total_tokens,0)), SUM(latency_ms)"
                " FROM request_log WHERE ts >= ? GROUP BY key_name, backend, model"
                " ORDER BY 4 DESC",
                (since,),
            ).fetchall()
