"""Thread-safe persistent content-addressed inference cache."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class InferenceCache:
    def __init__(self, path: Path, enabled: bool = True):
        self.path = Path(path).expanduser().resolve()
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def open(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._connection is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                timeout=30.0,
                check_same_thread=False,
                isolation_level=None,
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inference_cache (
                    cache_key TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    model TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._connection = connection

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        self.open()
        with self._lock:
            connection = self._require_connection()
            row = connection.execute(
                "SELECT response_json FROM inference_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE inference_cache SET last_used_at=?, hit_count=hit_count+1 WHERE cache_key=?",
                (time.time(), key),
            )
            payload = row[0]
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            # Corrupt rows must never become trusted model output.
            with self._lock:
                connection = self._require_connection()
                connection.execute("DELETE FROM inference_cache WHERE cache_key=?", (key,))
            return None

    def put(
        self,
        *,
        key: str,
        task: str,
        model: str,
        source_hash: str,
        prompt_hash: str,
        response: Any,
    ) -> None:
        if not self.enabled:
            return
        self.open()
        now = time.time()
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            connection = self._require_connection()
            connection.execute(
                """
                INSERT INTO inference_cache(
                    cache_key, task, model, source_hash, prompt_hash,
                    response_json, created_at, last_used_at, hit_count
                ) VALUES(?,?,?,?,?,?,?,?,0)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_json=excluded.response_json,
                    task=excluded.task,
                    model=excluded.model,
                    source_hash=excluded.source_hash,
                    prompt_hash=excluded.prompt_hash,
                    last_used_at=excluded.last_used_at
                """,
                (key, task, model, source_hash, prompt_hash, payload, now, now),
            )

    def close(self) -> None:
        with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                connection.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Inference cache connection is not open.")
        return self._connection

    def __enter__(self) -> "InferenceCache":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
