"""A thin wrapper over stdlib sqlite3. One file, no ORM.

Runs are persisted so a trace can be re-opened after the request that produced
it is gone. Connections are opened per call rather than shared: the workload is
one write per run, and per-call connections sidestep SQLite's thread-affinity
rules under uvicorn's threadpool entirely. The ``:memory:`` case is special —
an in-memory database dies with its connection — so that one is held open for
the life of the store, which is what tests want anyway.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import AgentResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        TEXT    NOT NULL,
    created_at        TEXT    NOT NULL,
    role_title        TEXT,
    jd_text           TEXT    NOT NULL,
    result_json       TEXT    NOT NULL,
    terminated_reason TEXT    NOT NULL,
    total_latency_ms  INTEGER NOT NULL,
    total_tokens      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at DESC);
"""


class RunStore:
    def __init__(self, path: str):
        self.path = path
        self._in_memory = path == ":memory:"
        self._lock = threading.Lock()
        self._shared: sqlite3.Connection | None = None
        if self._in_memory:
            self._shared = sqlite3.connect(path, check_same_thread=False)
            self._shared.row_factory = sqlite3.Row
        else:
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    # -- connection handling -------------------------------------------------

    class _Borrowed:
        """Context manager that commits, and only closes what it opened."""

        def __init__(self, conn: sqlite3.Connection, owned: bool):
            self._conn = conn
            self._owned = owned

        def __enter__(self) -> sqlite3.Connection:
            return self._conn

        def __exit__(self, exc_type, exc, tb) -> None:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
            if self._owned:
                self._conn.close()

    def _connect(self) -> "RunStore._Borrowed":
        if self._shared is not None:
            return self._Borrowed(self._shared, owned=False)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return self._Borrowed(conn, owned=True)

    # -- operations ----------------------------------------------------------

    def init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def ping(self) -> tuple[bool, str]:
        """Health probe: round-trip a real query, do not just open a file."""
        try:
            with self._lock, self._connect() as conn:
                conn.execute("SELECT COUNT(*) FROM runs").fetchone()
            return True, "ok"
        except sqlite3.Error as exc:
            return False, str(exc)

    def save_run(
        self,
        request_id: str,
        jd_text: str,
        result: AgentResult,
        role_title: str | None = None,
    ) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (request_id, created_at, role_title, jd_text, "
                "result_json, terminated_reason, total_latency_ms, total_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    datetime.now(timezone.utc).isoformat(),
                    role_title,
                    jd_text,
                    result.model_dump_json(),
                    result.terminated_reason,
                    result.total_latency_ms,
                    result.total_tokens,
                ),
            )
            return int(cur.lastrowid or 0)

    def load_run(self, run_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["result"] = AgentResult.model_validate_json(data.pop("result_json"))
        return data

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
