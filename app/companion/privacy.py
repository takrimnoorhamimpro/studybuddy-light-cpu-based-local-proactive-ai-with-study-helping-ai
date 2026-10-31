"""Privacy operations that cascade through local companion stores."""
from __future__ import annotations

import json
import time


class PrivacyService:
    def __init__(self, memory, proactive_limiter=None):
        self.memory = memory
        self.proactive_limiter = proactive_limiter

    def preview_forget(self, keyword: str) -> dict[str, int]:
        """Read-only per-table count of matching conversation/companion data and tasks."""
        keyword = str(keyword or "").strip()
        tables = {"turns": 0, "facts": 0, "notes": 0, "personal_nodes": 0,
                  "personal_edges": 0, "candidate_queue": 0, "companion_followups": 0, "open_loops": 0,
                  "companion_events": 0, "tasks": 0, "reminders": 0}
        if len(keyword) < 3:
            return tables
        like = f"%{keyword}%"
        with self.memory._db_lock:
            db = self.memory.db
            def has_table(name):
                return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
            tables["turns"] = db.execute("SELECT COUNT(*) FROM turns WHERE text LIKE ?", (like,)).fetchone()[0]
            tables["facts"] = db.execute("SELECT COUNT(*) FROM facts WHERE text LIKE ?", (like,)).fetchone()[0]
            tables["notes"] = db.execute("SELECT COUNT(*) FROM notes WHERE text LIKE ?", (like,)).fetchone()[0]
            node_ids = []
            if has_table("personal_nodes"):
                node_rows = db.execute("SELECT id FROM personal_nodes WHERE label LIKE ? OR evidence LIKE ?", (like, like)).fetchall()
                node_ids = [row["id"] for row in node_rows]
                tables["personal_nodes"] = len(node_ids)
            if has_table("candidate_queue"):
                if node_ids:
                    placeholders = ",".join("?" for _ in node_ids)
                    tables["candidate_queue"] = db.execute(f"SELECT COUNT(*) FROM candidate_queue WHERE node_id IN ({placeholders})", node_ids).fetchone()[0]
            if has_table("personal_edges"):
                if node_ids:
                    placeholders = ",".join("?" for _ in node_ids)
                    tables["personal_edges"] = db.execute(
                        f"SELECT COUNT(*) FROM personal_edges WHERE relation LIKE ? OR evidence LIKE ? "
                        f"OR source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                        [like, like] + node_ids + node_ids).fetchone()[0]
                else:
                    tables["personal_edges"] = db.execute("SELECT COUNT(*) FROM personal_edges WHERE relation LIKE ? OR evidence LIKE ?", (like, like)).fetchone()[0]
            if has_table("companion_followups"):
                tables["companion_followups"] = db.execute("SELECT COUNT(*) FROM companion_followups WHERE payload LIKE ?", (like,)).fetchone()[0]
            if has_table("open_loops"):
                if node_ids:
                    placeholders = ",".join("?" for _ in node_ids)
                    tables["open_loops"] = db.execute(
                        f"SELECT COUNT(*) FROM open_loops WHERE title LIKE ? OR node_id IN ({placeholders})",
                        [like] + node_ids).fetchone()[0]
                else:
                    tables["open_loops"] = db.execute("SELECT COUNT(*) FROM open_loops WHERE title LIKE ?", (like,)).fetchone()[0]
            if has_table("companion_events"):
                tables["companion_events"] = db.execute("SELECT COUNT(*) FROM companion_events WHERE reason LIKE ? OR meta LIKE ?", (like, like)).fetchone()[0]
            tables["tasks"] = db.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE ? OR details LIKE ?", (like, like)).fetchone()[0]
            tables["reminders"] = db.execute("SELECT COUNT(*) FROM reminders WHERE text LIKE ?", (like,)).fetchone()[0]
        return {key: int(value or 0) for key, value in tables.items()}

    def forget_topic(self, keyword: str, include_tasks: bool = False) -> int:
        """Forget conversation/companion data; tasks/reminders require explicit opt-in."""
        keyword = str(keyword or "").strip()
        if len(keyword) < 3:
            return 0
        like = f"%{keyword}%"
        total = 0
        with self.memory._db_lock:
            db = self.memory.db
            def has_table(name):
                return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
            db.execute("BEGIN IMMEDIATE")
            try:
                total += db.execute("DELETE FROM turns WHERE text LIKE ?", (like,)).rowcount
                total += db.execute("DELETE FROM facts WHERE text LIKE ?", (like,)).rowcount
                note_rows = db.execute("SELECT id,text FROM notes WHERE text LIKE ?", (like,)).fetchall()
                for row in note_rows:
                    if getattr(self.memory, "fts", False):
                        db.execute("INSERT INTO notes_fts(notes_fts,rowid,text) VALUES('delete',?,?)", (row["id"], row["text"]))
                    db.execute("DELETE FROM notes WHERE id=?", (row["id"],))
                total += len(note_rows)

                node_ids = []
                if has_table("personal_nodes"):
                    node_rows = db.execute(
                        "SELECT id FROM personal_nodes WHERE label LIKE ? OR evidence LIKE ?", (like, like)
                    ).fetchall()
                    node_ids = [row["id"] for row in node_rows]
                if node_ids:
                    placeholders = ",".join("?" for _ in node_ids)
                    loop_ids=[]
                    if has_table("open_loops"):
                        loop_ids=[str(r[0]) for r in db.execute(f"SELECT id FROM open_loops WHERE node_id IN ({placeholders})",node_ids).fetchall()]
                    if has_table("candidate_queue"):
                        total += db.execute(f"DELETE FROM candidate_queue WHERE node_id IN ({placeholders})", node_ids).rowcount
                    if has_table("personal_edges"):
                        total += db.execute(
                            f"DELETE FROM personal_edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                            node_ids + node_ids,
                        ).rowcount
                    if has_table("open_loops"):
                        total += db.execute(f"DELETE FROM open_loops WHERE node_id IN ({placeholders})", node_ids).rowcount
                    if has_table("companion_events"):
                        for ref_id in node_ids + loop_ids:
                            total += db.execute("DELETE FROM companion_events WHERE meta LIKE ?", (f'%{ref_id}%',)).rowcount
                    total += db.execute(f"DELETE FROM personal_nodes WHERE id IN ({placeholders})", node_ids).rowcount
                if has_table("personal_edges"):
                    total += db.execute("DELETE FROM personal_edges WHERE relation LIKE ? OR evidence LIKE ?", (like, like)).rowcount
                if has_table("companion_followups"):
                    total += db.execute("DELETE FROM companion_followups WHERE payload LIKE ?", (like,)).rowcount
                if has_table("open_loops"):
                    total += db.execute("DELETE FROM open_loops WHERE title LIKE ?", (like,)).rowcount
                if has_table("companion_events"):
                    total += db.execute("DELETE FROM companion_events WHERE reason LIKE ? OR meta LIKE ?", (like, like)).rowcount
                if include_tasks:
                    total += db.execute("DELETE FROM tasks WHERE title LIKE ? OR details LIKE ?", (like, like)).rowcount
                    total += db.execute("DELETE FROM reminders WHERE text LIKE ?", (like,)).rowcount
                # Keep a privacy-action audit without retaining the forgotten keyword.
                if total and has_table("consent_ledger"):
                    db.execute(
                        "INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)",
                        (time.time(), "facts", 0, "topic_forget_requested"),
                    )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return total

    def wipe_companion_data(self) -> dict:
        """Wipe conversation/personal-companion data, preserving tasks and reminders."""
        tables = ("turns", "facts", "notes", "candidate_queue", "personal_edges", "personal_nodes", "companion_followups",
                  "companion_settings", "companion_events", "rhythm_buckets", "rhythm_days", "open_loops", "trigger_stats",
                  "mood_daily", "growth_log")
        counts = {}
        with self.memory._db_lock:
            db = self.memory.db
            db.execute("BEGIN IMMEDIATE")
            try:
                for table in tables:
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                    if exists:
                        counts[table] = db.execute(f"DELETE FROM {table}").rowcount
                if getattr(self.memory, "fts", False):
                    db.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
                db.commit()
            except Exception:
                db.rollback()
                raise
        if self.proactive_limiter: self.proactive_limiter.clear_history()
        return counts


    def _audit(self, scope, note):
        with self.memory._db_lock:
            exists=self.memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='consent_ledger'").fetchone()
            if exists:
                self.memory.db.execute("INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)",(time.time(),scope,0,note)); self.memory.db.commit()

    def delete_rhythm(self) -> int:
        with self.memory._db_lock:
            cur=self.memory.db.execute("DELETE FROM rhythm_buckets"); count=cur.rowcount
            self.memory.db.execute("DELETE FROM rhythm_days"); self.memory.db.commit()
        self._audit("rhythm","learned_patterns_deleted"); return count

    def delete_nudge_history(self) -> dict:
        with self.memory._db_lock:
            events=self.memory.db.execute("DELETE FROM companion_events"); stats=self.memory.db.execute("DELETE FROM trigger_stats")
            self.memory.db.execute("DELETE FROM companion_settings WHERE key LIKE 'trigger_halved_until_%' OR key LIKE 'trigger_mute_until_%' OR key='initiative_last_nudge_ts'")
            self.memory.db.commit(); result={"companion_events":events.rowcount,"trigger_stats":stats.rowcount}
        if self.proactive_limiter: self.proactive_limiter.clear_history()
        self._audit("proactive","nudge_history_deleted"); return result

    def delete_all_proactive_data(self) -> dict:
        with self.memory._db_lock:
            counts={}
            for table in ("open_loops","rhythm_buckets","rhythm_days","companion_events","trigger_stats"):
                counts[table]=self.memory.db.execute(f"DELETE FROM {table}").rowcount
            self.memory.db.execute("DELETE FROM companion_settings WHERE key LIKE 'trigger_halved_until_%' OR key LIKE 'trigger_mute_until_%' OR key='initiative_last_nudge_ts'")
            self.memory.db.commit()
        if self.proactive_limiter: self.proactive_limiter.clear_history()
        self._audit("proactive","all_proactive_data_deleted"); return counts

    def export_json(self) -> dict:
        """Export local personal/companion records without credentials or raw window titles."""
        out = {"exported_ts": time.time(), "tables": {}}
        names = ("turns", "profile", "trusted_people", "facts", "notes", "tasks", "reminders",
                 "personal_nodes", "personal_edges", "candidate_queue", "companion_followups", "companion_settings",
                 "consent_ledger", "companion_events", "rhythm_buckets", "rhythm_days", "open_loops", "trigger_stats",
                 "mood_daily", "growth_log")
        with self.memory._db_lock:
            for table in names:
                exists = self.memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                if exists:
                    rows = self.memory.db.execute(f"SELECT * FROM {table}").fetchall()
                    out["tables"][table] = [dict(row) for row in rows]
        return out
