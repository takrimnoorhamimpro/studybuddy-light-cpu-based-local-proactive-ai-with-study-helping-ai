"""Backup-first, additive SQLite migrations for companion data."""
from __future__ import annotations

import os
import shutil
import time

SCHEMA_VERSION = 3


def _columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _backup_if_needed(memory, version):
    path = getattr(memory, "db_path", None)
    if not path or path == ":memory:" or not os.path.isfile(path) or version >= SCHEMA_VERSION:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    backup = f"{path}.bak-{stamp}"
    # SQLite's backup API yields a consistent copy even if the database uses WAL.
    import sqlite3
    with memory._db_lock:
        target = sqlite3.connect(backup)
        try:
            memory.db.backup(target)
        finally:
            target.close()
    return backup


def migrate(memory):
    """Bring companion schema to v3 additively; scrub sensitive legacy evidence in place."""
    db = memory.db
    with memory._db_lock:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version >= SCHEMA_VERSION:
            return None
        # Some callers may have performed direct SQLite writes before first companion
        # initialization. Commit those existing writes before backup/migration starts.
        if db.in_transaction:
            db.commit()
        backup = _backup_if_needed(memory, version)
        db.execute("BEGIN IMMEDIATE")
        try:
            # Create the v1 base tables if this is a fresh DB. Existing tables remain intact.
            db.execute("""CREATE TABLE IF NOT EXISTS personal_nodes(
                id TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate', evidence TEXT NOT NULL DEFAULT '',
                consented INTEGER NOT NULL DEFAULT 0, created_ts REAL NOT NULL, updated_ts REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS personal_edges(
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                relation TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '', created_ts REAL NOT NULL,
                FOREIGN KEY(source_id) REFERENCES personal_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY(target_id) REFERENCES personal_nodes(id) ON DELETE CASCADE)""")
            db.execute("""CREATE TABLE IF NOT EXISTS companion_followups(
                id TEXT PRIMARY KEY, due_ts REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', created_ts REAL NOT NULL, delivered_ts REAL)""")
            db.execute("CREATE TABLE IF NOT EXISTS companion_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS candidate_queue(node_id TEXT PRIMARY KEY, queued_ts REAL NOT NULL, deferrals INTEGER NOT NULL DEFAULT 0, last_prompted_ts REAL, FOREIGN KEY(node_id) REFERENCES personal_nodes(id) ON DELETE CASCADE)")

            node_columns = _columns(db, "personal_nodes")
            additions = {
                "importance": "REAL NOT NULL DEFAULT 0.5",
                "last_used_ts": "REAL",
                "use_count": "INTEGER NOT NULL DEFAULT 0",
                "expires_ts": "REAL",
                "source": "TEXT NOT NULL DEFAULT 'manual'",
                "sensitivity": "TEXT NOT NULL DEFAULT 'normal'",
            }
            for name, decl in additions.items():
                if name not in node_columns:
                    db.execute(f"ALTER TABLE personal_nodes ADD COLUMN {name} {decl}")
            followup_columns = _columns(db, "companion_followups")
            if "claimed_ts" not in followup_columns:
                db.execute("ALTER TABLE companion_followups ADD COLUMN claimed_ts REAL")

            db.execute("""CREATE TABLE IF NOT EXISTS consent_ledger(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, scope TEXT NOT NULL,
                granted INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '')""")
            db.execute("""CREATE TABLE IF NOT EXISTS companion_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
                trigger TEXT, reason TEXT, meta TEXT NOT NULL DEFAULT '{}')""")
            db.execute("""CREATE TABLE IF NOT EXISTS rhythm_buckets(
                dow INTEGER NOT NULL, hour INTEGER NOT NULL, app_category TEXT NOT NULL,
                minutes REAL NOT NULL DEFAULT 0, samples INTEGER NOT NULL DEFAULT 0, updated_ts REAL NOT NULL,
                PRIMARY KEY(dow,hour,app_category))""")
            db.execute("""CREATE TABLE IF NOT EXISTS open_loops(
                id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, due_ts REAL,
                status TEXT NOT NULL DEFAULT 'open', source TEXT NOT NULL, node_id TEXT,
                created_ts REAL NOT NULL, last_surfaced_ts REAL, surfaced_count INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS trigger_stats(
                trigger TEXT NOT NULL, hour_bucket INTEGER NOT NULL, alpha REAL NOT NULL DEFAULT 1,
                beta REAL NOT NULL DEFAULT 1, updated_ts REAL NOT NULL,
                PRIMARY KEY(trigger,hour_bucket))""")
            db.execute("CREATE TABLE IF NOT EXISTS mood_daily(day TEXT PRIMARY KEY, valence REAL NOT NULL, n INTEGER NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS growth_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, field TEXT NOT NULL,
                old TEXT, new TEXT, reason TEXT NOT NULL, reverted INTEGER NOT NULL DEFAULT 0)""")
            # v3 additions are strictly additive and preserve existing rows.
            loop_columns = _columns(db, "open_loops")
            for name, decl in {"step": "INTEGER NOT NULL DEFAULT 0", "snoozed_until": "REAL", "ref_id": "TEXT"}.items():
                if name not in loop_columns:
                    db.execute(f"ALTER TABLE open_loops ADD COLUMN {name} {decl}")
            event_columns = _columns(db, "companion_events")
            if "nudge_id" not in event_columns:
                db.execute("ALTER TABLE companion_events ADD COLUMN nudge_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON companion_events(ts)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_events_nudge ON companion_events(nudge_id)")
            db.execute("CREATE TABLE IF NOT EXISTS rhythm_days(day TEXT PRIMARY KEY, updated_ts REAL NOT NULL)")
            # Evidence can contain the original sentence, even when the label was clean.
            # Keep useful labels but erase any evidence rejected by the current filter.
            from companion.sensitive import is_sensitive
            for row in db.execute("SELECT id,evidence FROM personal_nodes WHERE COALESCE(evidence,'')<>''").fetchall():
                if is_sensitive(row["evidence"]):
                    db.execute("UPDATE personal_nodes SET evidence='' WHERE id=?", (row["id"],))
            # Explicitly preserve existing nodes while ensuring the new queue is available.
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        except Exception:
            db.rollback()
            raise
        return backup
