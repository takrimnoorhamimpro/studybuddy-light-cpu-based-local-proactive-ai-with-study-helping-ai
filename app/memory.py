"""
memory.py  -  Memory v1 (Section 6.5 of the design). One SQLite file per user.

Think of it as a diary with a small index:
  turns          every chat message (raw; will be summarised and deleted later)
  facts          stable things about the owner ("exam on 20 Nov")
  trusted_people people the owner trusts (used by the safety replies)
  notes          summaries: daily / weekly / monthly / yearly (the "sleep" job fills these)

Retrieval never reads everything: it returns only the best few notes.

Use Memory(":memory:") in tests so nothing is written to disk.
Encryption is NOT added yet (open decision 8) - that is a later step.
"""

import os
import re
import sqlite3
import threading
import time
from functools import wraps


def _db_locked(method):
    """Serialize SQLite access when the GUI and reply worker share Memory."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._db_lock:
            return method(self, *args, **kwargs)
    return wrapper

_WORD = re.compile(r"[a-z0-9']+")
_STOP = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "i", "me",
    "my", "you", "your", "to", "of", "in", "on", "at", "it", "this", "that",
    "do", "did", "have", "has", "had", "be", "so", "for", "with", "about",
    "what", "how", "why", "can", "just", "am", "im", "i'm", "not", "no", "yes",
}


def _keywords(text, limit=8):
    words = []
    for w in _WORD.findall(text.lower()):
        w = w.strip("'")
        if len(w) > 2 and w not in _STOP and w not in words:
            words.append(w)
    return words[:limit]


class Memory:
    def __init__(self, db_path):
        self.db_path = db_path
        if db_path != ":memory:":
            folder = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(folder, exist_ok=True)
        # The desktop GUI calls memory from both its UI thread and reply worker.
        # Disable SQLite's thread-affinity check and serialize every DB operation.
        self._db_lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self._create_tables()

    # ---------- setup ----------
    @_db_locked
    def _create_tables(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL, text TEXT NOT NULL UNIQUE, source TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trusted_people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS profile (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL, level TEXT NOT NULL,
                period TEXT NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
                due_ts REAL, status TEXT NOT NULL DEFAULT 'open',
                created_ts REAL NOT NULL, completed_ts REAL);
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL, remind_ts REAL NOT NULL,
                fired INTEGER NOT NULL DEFAULT 0, created_ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS personality_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_ts REAL NOT NULL, status TEXT NOT NULL,
                reason TEXT NOT NULL, character_json TEXT NOT NULL,
                evaluation_json TEXT NOT NULL DEFAULT '{}');
        """)
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
                "USING fts5(text, content='notes', content_rowid='id')")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False   # fall back to simple LIKE search
        self.db.commit()

    # ---------- turns ----------
    @_db_locked
    def add_turn(self, role, text):
        self.db.execute("INSERT INTO turns (ts, role, text) VALUES (?,?,?)",
                        (time.time(), role, text))
        self.db.commit()

    @_db_locked
    def recent_turns(self, n, max_age_hours=None):
        """Last n turns, oldest first, as [{"role","content"}] for the prompt.
        max_age_hours: ignore turns older than this (None = no limit)."""
        if n <= 0:
            return []
        if max_age_hours:
            cutoff = time.time() - max_age_hours * 3600
            rows = self.db.execute(
                "SELECT role, text FROM turns WHERE ts >= ? ORDER BY id DESC LIMIT ?",
                (cutoff, n)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT role, text FROM turns ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [{"role": r["role"], "content": r["text"]} for r in reversed(rows)]

    @_db_locked
    def turns_after(self, turn_id, limit=24):
        """Return a bounded, ordered batch of saved turns after an integer cursor."""
        try:
            cursor = max(0, int(turn_id))
        except (TypeError, ValueError):
            cursor = 0
        try:
            limit = max(1, min(60, int(limit)))
        except (TypeError, ValueError):
            limit = 24
        rows = self.db.execute(
            "SELECT id, ts, role, text FROM turns WHERE id>? ORDER BY id ASC LIMIT ?",
            (cursor, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    @_db_locked
    def count_turns_after(self, turn_id):
        """Count retained raw chat turns after a review cursor."""
        try:
            cursor = max(0, int(turn_id))
        except (TypeError, ValueError):
            cursor = 0
        row = self.db.execute("SELECT COUNT(*) AS n FROM turns WHERE id>?", (cursor,)).fetchone()
        return int(row["n"] or 0)

    @_db_locked
    def clear_turns(self):
        """Delete the whole saved chat history (facts and notes stay)."""
        n = self.db.execute("DELETE FROM turns").rowcount
        self.db.commit()
        return n

    @_db_locked
    def turns_for_day(self, day):
        """Return turns for a local calendar date (YYYY-MM-DD), oldest first."""
        rows = self.db.execute(
            "SELECT id, ts, role, text FROM turns "
            "WHERE date(ts, 'unixepoch', 'localtime')=? ORDER BY id", (day,)
        ).fetchall()
        return [dict(r) for r in rows]

    @_db_locked
    def days_with_turns_before(self, cutoff_ts):
        rows = self.db.execute(
            "SELECT DISTINCT date(ts, 'unixepoch', 'localtime') AS day FROM turns "
            "WHERE ts < ? ORDER BY day", (float(cutoff_ts),)
        ).fetchall()
        return [r['day'] for r in rows if r['day']]

    @_db_locked
    def delete_turn_ids(self, turn_ids):
        ids = [int(x) for x in turn_ids]
        if not ids:
            return 0
        marks = ','.join('?' for _ in ids)
        cur = self.db.execute(f"DELETE FROM turns WHERE id IN ({marks})", ids)
        self.db.commit()
        return cur.rowcount

    @_db_locked
    def upsert_note(self, level, period, text):
        """Replace one roll-up note atomically, keeping the FTS index in sync."""
        text = (text or '').strip()
        if not text:
            raise ValueError('A memory summary cannot be empty.')
        old_rows = self.db.execute(
            "SELECT id, text FROM notes WHERE level=? AND period=? ORDER BY id DESC",
            (level, period)).fetchall()
        if old_rows:
            if self.fts:
                for old in old_rows:
                    try:
                        self.db.execute("INSERT INTO notes_fts(notes_fts,rowid,text) VALUES('delete',?,?)",
                                        (old['id'], old['text']))
                    except sqlite3.OperationalError:
                        pass
            self.db.execute("DELETE FROM notes WHERE level=? AND period=?", (level, period))
        cur = self.db.execute(
            "INSERT INTO notes (ts, level, period, text) VALUES (?,?,?,?)",
            (time.time(), level, period, text))
        if self.fts:
            self.db.execute("INSERT INTO notes_fts(rowid,text) VALUES (?,?)", (cur.lastrowid, text))
        self.db.commit()
        return cur.lastrowid

    @_db_locked
    def notes_for_periods(self, level, start_period, end_period):
        rows = self.db.execute(
            "SELECT id, ts, level, period, text FROM notes "
            "WHERE level=? AND period>=? AND period<=? ORDER BY period, id",
            (level, start_period, end_period)).fetchall()
        return [dict(r) for r in rows]

    @_db_locked
    def add_personality_version(self, status, reason, character, evaluation=None):
        import json
        cur = self.db.execute(
            "INSERT INTO personality_versions(created_ts,status,reason,character_json,evaluation_json) "
            "VALUES (?,?,?,?,?)",
            (time.time(), status, reason, json.dumps(character, ensure_ascii=False),
             json.dumps(evaluation or {}, ensure_ascii=False)))
        self.db.commit()
        return cur.lastrowid

    @_db_locked
    def set_personality_version_status(self, version_id, status):
        cur = self.db.execute("UPDATE personality_versions SET status=? WHERE id=?", (status, int(version_id)))
        self.db.commit()
        return cur.rowcount > 0

    @_db_locked
    def list_personality_versions(self, limit=20):
        import json
        rows = self.db.execute(
            "SELECT * FROM personality_versions ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try: item['character'] = json.loads(item.pop('character_json'))
            except Exception: item['character'] = {}
            try: item['evaluation'] = json.loads(item.pop('evaluation_json'))
            except Exception: item['evaluation'] = {}
            result.append(item)
        return result

    @_db_locked
    def claim_due_reminders(self, now=None):
        """Atomically claim reminders so the UI and background agent cannot double-fire."""
        now = time.time() if now is None else float(now)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            rows = self.db.execute(
                "SELECT id,text,remind_ts FROM reminders WHERE fired=0 AND remind_ts<=? ORDER BY remind_ts",
                (now,)).fetchall()
            ids = [r['id'] for r in rows]
            if ids:
                marks = ','.join('?' for _ in ids)
                self.db.execute(f"UPDATE reminders SET fired=1 WHERE id IN ({marks})", ids)
            self.db.commit()
            return [dict(r) for r in rows]
        except Exception:
            self.db.rollback()
            raise

    # ---------- profile (names etc.) ----------
    @_db_locked
    def set_profile(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO profile (key, value) VALUES (?,?)",
                        (key, value.strip()))
        self.db.commit()

    @_db_locked
    def get_profile(self, key, default=None):
        row = self.db.execute("SELECT value FROM profile WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    @_db_locked
    def list_turns(self, limit=100):
        rows = self.db.execute("SELECT id, ts, role, text FROM turns ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    @_db_locked
    def search_turns(self, query="", limit=12):
        """Rank retained conversation turns with BM25-style lexical retrieval.

        This is bounded to the newest 3,000 turns to keep CPU/RAM predictable.
        Empty queries return recent turns, which is useful for a broad request to
        recap prior chats. Specific queries rank by term rarity, frequency and
        document length rather than raw word counts alone.
        """
        from collections import Counter
        import math
        limit = max(1, min(int(limit), 100))
        query = (query or "").strip().casefold()
        rows = self.db.execute(
            "SELECT id, ts, role, text FROM turns ORDER BY id DESC LIMIT 3000"
        ).fetchall()
        items = [dict(row) for row in rows]
        if not query:
            return items[:limit]

        def norm(token):
            token = token.casefold()
            if len(token) > 5 and token.endswith("ing"):
                token = token[:-3]
                if len(token) > 2 and token[-1] == token[-2]:
                    token = token[:-1]
            elif len(token) > 4 and token.endswith("ed"):
                token = token[:-2]
                if token.endswith("t"):
                    token += "e"
            elif len(token) > 4 and token.endswith("ies"):
                token = token[:-3] + "y"
            elif len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "ous", "us", "is")):
                token = token[:-1]
            return token

        terms = {norm(w) for w in _keywords(query, limit=12)}
        if not terms:
            return []
        docs = []
        for item in items:
            tokens = [norm(w) for w in _WORD.findall(str(item.get("text", "")).casefold())]
            tokens = [w for w in tokens if len(w) > 2 and w not in _STOP]
            docs.append(tokens)
        document_frequency = Counter(term for doc in docs for term in set(doc))
        average_length = sum(max(1, len(doc)) for doc in docs) / max(1, len(docs))
        total_docs = max(1, len(docs))
        k1, b = 1.5, 0.75
        scored = []
        for index, (item, tokens) in enumerate(zip(items, docs)):
            counts = Counter(tokens)
            doc_len = max(1, len(tokens))
            score = 0.0
            for term in terms.intersection(counts):
                df = document_frequency[term]
                idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
                tf = counts[term]
                score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / average_length))
            body = str(item.get("text", "")).casefold()
            if query in body:
                score += 2.0
            if score > 0:
                # Small recency preference only breaks nearly equal scores.
                score += 0.001 / (index + 1)
                scored.append((score, int(item["id"]), item))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [row[2] for row in scored[:limit]]

    @_db_locked
    def delete_turn(self, turn_id):
        cur = self.db.execute("DELETE FROM turns WHERE id=?", (int(turn_id),))
        self.db.commit()
        return cur.rowcount > 0

    @_db_locked
    def list_notes(self, limit=100):
        rows = self.db.execute("SELECT id, ts, level, period, text FROM notes ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    @_db_locked
    def delete_note(self, note_id):
        row = self.db.execute("SELECT text FROM notes WHERE id=?", (int(note_id),)).fetchone()
        if not row: return False
        if self.fts:
            try:
                self.db.execute("INSERT INTO notes_fts(notes_fts, rowid, text) VALUES('delete', ?, ?)", (int(note_id), row["text"]))
            except sqlite3.OperationalError:
                pass
        cur = self.db.execute("DELETE FROM notes WHERE id=?", (int(note_id),))
        self.db.commit()
        return cur.rowcount > 0

    # ---------- facts ----------
    @_db_locked
    def add_fact(self, text, source="owner"):
        text = text.strip()
        if not text:
            return False
        cur = self.db.execute(
            "INSERT OR IGNORE INTO facts (ts, text, source) VALUES (?,?,?)",
            (time.time(), text, source))
        self.db.commit()
        return cur.rowcount > 0

    @_db_locked
    def list_facts(self, limit=8):
        rows = self.db.execute(
            "SELECT text FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [r["text"] for r in rows]

    # ---------- trusted people ----------
    @_db_locked
    def add_trusted(self, name):
        name = name.strip()
        if not name:
            return False
        cur = self.db.execute(
            "INSERT OR IGNORE INTO trusted_people (name) VALUES (?)", (name,))
        self.db.commit()
        return cur.rowcount > 0

    @_db_locked
    def delete_trusted(self, name):
        cur = self.db.execute("DELETE FROM trusted_people WHERE name=? COLLATE NOCASE", ((name or "").strip(),))
        self.db.commit()
        return cur.rowcount > 0

    @_db_locked
    def trusted_names(self):
        rows = self.db.execute("SELECT name FROM trusted_people ORDER BY id").fetchall()
        return [r["name"] for r in rows]

    # ---------- persistent tasks and reminders ----------
    @_db_locked
    def add_task(self, title, details="", due_ts=None):
        title = (title or "").strip()
        if not title:
            return None
        cur = self.db.execute(
            "INSERT INTO tasks (title, details, due_ts, status, created_ts) VALUES (?,?,?,'open',?)",
            (title, (details or "").strip(), due_ts, time.time()))
        self.db.commit()
        return cur.lastrowid

    @_db_locked
    def list_tasks(self, include_done=False):
        if include_done:
            rows = self.db.execute(
                "SELECT * FROM tasks ORDER BY CASE WHEN status='open' THEN 0 ELSE 1 END, COALESCE(due_ts, 4102444800), id DESC").fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM tasks WHERE status='open' ORDER BY COALESCE(due_ts, 4102444800), id DESC").fetchall()
        return [dict(r) for r in rows]

    @_db_locked
    def search_tasks(self, query, limit=3, status="open"):
        """Retrieve task records relevant to a context using BM25 ranking.

        This is used by the proactive context builder to connect an active app/title
        with a user's actual goals, without matching specific phrases or dumping the
        entire task list into the prompt. Only scored matches are returned.
        """
        import math
        from collections import Counter

        query = (query or "").strip().casefold()
        limit = max(1, min(int(limit), 20))
        if not query:
            return []
        allowed_status = {"open", "done", "all"}
        status = str(status or "open").casefold()
        if status not in allowed_status:
            status = "open"
        if status == "all":
            rows = self.db.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT 1000").fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY id DESC LIMIT 1000", (status,)).fetchall()
        items = [dict(row) for row in rows]
        if not items:
            return []

        def norm(token):
            token = token.casefold()
            if len(token) > 5 and token.endswith("ing"):
                token = token[:-3]
                if len(token) > 2 and token[-1] == token[-2]:
                    token = token[:-1]
            elif len(token) > 4 and token.endswith("ed"):
                token = token[:-2]
                if token.endswith("t"):
                    token += "e"
            elif len(token) > 4 and token.endswith("ies"):
                token = token[:-3] + "y"
            elif len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "ous", "us", "is")):
                token = token[:-1]
            return token

        terms = {norm(word) for word in _keywords(query, limit=20)}
        if not terms:
            return []
        docs = []
        for item in items:
            text = str(item.get("title", "")) + " " + str(item.get("details", ""))
            tokens = [norm(word) for word in _WORD.findall(text.casefold())]
            docs.append([word for word in tokens if len(word) > 2 and word not in _STOP])
        counts = [Counter(doc) for doc in docs]
        df = Counter(term for doc in docs for term in set(doc))
        average_length = sum(max(1, len(doc)) for doc in docs) / max(1, len(docs))
        total_docs = len(docs)
        k1, b = 1.5, 0.75
        ranked = []
        for item, tokens, frequencies in zip(items, docs, counts):
            length = max(1, len(tokens))
            score = 0.0
            for term in terms.intersection(frequencies):
                idf = math.log(1 + (total_docs - df[term] + 0.5) / (df[term] + 0.5))
                tf = frequencies[term]
                score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * length / average_length))
            title_tokens = {norm(w) for w in _WORD.findall(str(item.get("title", "")).casefold())}
            if terms.intersection(title_tokens):
                score += 0.25
            if score > 0:
                ranked.append((score, int(item["id"]), item))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [row[2] for row in ranked[:limit]
                if row[0] >= max(0.15, ranked[0][0] * 0.2)]

    @_db_locked
    def complete_task(self, task_id):
        cur = self.db.execute("UPDATE tasks SET status='done', completed_ts=? WHERE id=? AND status='open'", (time.time(), int(task_id)))
        self.db.commit()
        return cur.rowcount > 0

    @_db_locked
    def delete_task(self, task_id):
        cur = self.db.execute("DELETE FROM tasks WHERE id=?", (int(task_id),))
        self.db.commit()
        return cur.rowcount > 0

    @_db_locked
    def add_reminder(self, text, remind_ts):
        text = (text or "").strip()
        if not text: return None
        cur = self.db.execute("INSERT INTO reminders (text, remind_ts, fired, created_ts) VALUES (?,?,0,?)", (text, float(remind_ts), time.time()))
        self.db.commit()
        return cur.lastrowid

    @_db_locked
    def due_reminders(self, now=None):
        now = time.time() if now is None else float(now)
        rows = self.db.execute("SELECT id, text, remind_ts FROM reminders WHERE fired=0 AND remind_ts<=? ORDER BY remind_ts", (now,)).fetchall()
        return [dict(r) for r in rows]

    @_db_locked
    def mark_reminder_fired(self, reminder_id):
        self.db.execute("UPDATE reminders SET fired=1 WHERE id=?", (int(reminder_id),))
        self.db.commit()

    @_db_locked
    def list_reminders(self, include_fired=False):
        if include_fired:
            rows = self.db.execute("SELECT * FROM reminders ORDER BY remind_ts DESC").fetchall()
        else:
            rows = self.db.execute("SELECT * FROM reminders WHERE fired=0 ORDER BY remind_ts").fetchall()
        return [dict(r) for r in rows]

    # ---------- notes (summaries) ----------
    @_db_locked
    def add_note(self, level, period, text):
        """level: daily/weekly/monthly/yearly. period: e.g. '2026-10-15'."""
        cur = self.db.execute(
            "INSERT INTO notes (ts, level, period, text) VALUES (?,?,?,?)",
            (time.time(), level, period, text.strip()))
        if self.fts:
            self.db.execute("INSERT INTO notes_fts(rowid, text) VALUES (?,?)",
                            (cur.lastrowid, text.strip()))
        self.db.commit()
        return cur.lastrowid

    @_db_locked
    def search_notes(self, query, k=4):
        """Best k notes for this message (keyword match). Returns list of strings."""
        words = _keywords(query)
        if not words or k <= 0:
            return []
        if self.fts:
            expr = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
            try:
                rows = self.db.execute(
                    "SELECT n.period, n.text FROM notes_fts f "
                    "JOIN notes n ON n.id = f.rowid "
                    "WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?", (expr, k)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        else:
            like = " OR ".join(["text LIKE ?"] * len(words))
            rows = self.db.execute(
                "SELECT period, text FROM notes WHERE " + like + " LIMIT ?",
                ["%" + w + "%" for w in words] + [k]).fetchall()
        return [r["period"] + ": " + r["text"] for r in rows]

    # ---------- forget ----------
    @_db_locked
    def forget(self, keyword, include_tasks=False):
        """Forget conversation/companion memory; tasks/reminders need explicit opt-in."""
        from companion.privacy import PrivacyService
        return PrivacyService(self).forget_topic(keyword, include_tasks=include_tasks)

    @_db_locked
    def close(self):
        self.db.close()
