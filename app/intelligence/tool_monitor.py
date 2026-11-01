"""Privacy-conscious SQLite telemetry for actual Study Buddy tool executions."""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import os
import sqlite3
import threading
import time


class ToolMonitor:
    def __init__(self, db_path: str, retention: int = 2500):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.db_path = db_path
        self.retention = max(200, int(retention))
        self._lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS tool_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL,
                duration_ms INTEGER,
                summary TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_tool_events_started ON tool_events(started_at DESC)")
        self.db.commit()

    def start(self, tool_id: str, tool_name: str, source: str, summary: str = "") -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO tool_events(tool_id,tool_name,source,status,started_at,summary) VALUES(?,?,?,?,?,?)",
                (tool_id, tool_name, source, "running", time.time(), summary[:240]),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def finish(self, event_id: int, status: str = "success", error: str = "") -> None:
        finished = time.time()
        with self._lock:
            row = self.db.execute("SELECT started_at FROM tool_events WHERE id=?", (event_id,)).fetchone()
            if not row:
                return
            duration = max(0, int((finished - float(row["started_at"])) * 1000))
            self.db.execute(
                "UPDATE tool_events SET status=?,finished_at=?,duration_ms=?,error=? WHERE id=?",
                (status, finished, duration, str(error)[:500], event_id),
            )
            self.db.execute("DELETE FROM tool_events WHERE id NOT IN (SELECT id FROM tool_events ORDER BY id DESC LIMIT ?)",
                            (self.retention,))
            self.db.commit()

    @contextmanager
    def track(self, tool_id: str, tool_name: str, source: str, summary: str = ""):
        event_id = self.start(tool_id, tool_name, source, summary)
        try:
            yield event_id
        except Exception as exc:
            # Exception messages can sometimes echo user-provided values; keep
            # the event useful without copying potentially private content.
            self.finish(event_id, "failed", f"{type(exc).__name__}: details omitted for privacy")
            raise
        else:
            self.finish(event_id, "success")

    def instrument(self, target, method_name: str, tool_id: str, tool_name: str,
                   source: str, summary: str = "") -> bool:
        """Wrap a bound method once; never persist raw arguments or user text."""
        original = getattr(target, method_name, None)
        if not callable(original) or getattr(original, "_studybuddy_monitored", False):
            return False

        @wraps(original)
        def monitored(*args, **kwargs):
            with self.track(tool_id, tool_name, source, summary):
                return original(*args, **kwargs)

        monitored._studybuddy_monitored = True
        setattr(target, method_name, monitored)
        return True

    def recent_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM tool_events ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def latency_summary(self, tool_id: str = "local_ai") -> dict:
        """Return real observed latency only; no synthetic estimates are created."""
        with self._lock:
            rows = self.db.execute(
                "SELECT duration_ms FROM tool_events WHERE tool_id=? AND duration_ms IS NOT NULL "
                "AND status='success' ORDER BY id DESC LIMIT 100", (str(tool_id),)
            ).fetchall()
        values = [max(0, int(row["duration_ms"])) for row in rows if row["duration_ms"] is not None]
        if not values:
            return {"samples": 0, "average_ms": None, "last_ms": None}
        return {"samples": len(values), "average_ms": int(sum(values) / len(values)), "last_ms": values[0]}

    def active_count(self) -> int:
        with self._lock:
            row = self.db.execute("SELECT COUNT(*) AS n FROM tool_events WHERE status='running'").fetchone()
            return int(row["n"])

    def running_tool_ids(self) -> set[str]:
        with self._lock:
            rows = self.db.execute("SELECT DISTINCT tool_id FROM tool_events WHERE status='running'").fetchall()
        return {str(row["tool_id"]) for row in rows}

    def stats(self) -> dict:
        with self._lock:
            row = self.db.execute("""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS succeeded,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running
                FROM tool_events
            """).fetchone()
        return {key: int(row[key] or 0) for key in ("total", "succeeded", "failed", "running")}

    def close(self):
        with self._lock:
            try:
                self.db.close()
            except Exception:
                pass
