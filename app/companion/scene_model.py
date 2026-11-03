"""Self-learning situation model for the camera's scene descriptions.

No situation, object or keyword is predefined. The model:
  1. turns every vision-model description into a sparse TF-IDF vector (hashed words + word pairs,
     light suffix stripping, IDF learned online);
  2. discovers "situations" by online leader-follower clustering of those vectors (and re-fits the
     whole clustering from stored observations at a few growth milestones, because early IDF is noisy);
  3. debounces noisy observations into visits (a switch needs two consecutive agreeing observations);
  4. learns the user's own dwell-time baseline per situation and time of day (robust median/MAD/p80)
     and how typical each situation is for that time of day (smoothed frequency -> surprise);
  5. proposes a nudge only when the current visit is unusually long for THIS user and situation, after
     a learning period; and
  6. learns which situations/hours the user welcomes nudges in with a per-arm Beta posterior sampled
     by Thompson sampling, updated from the existing nudge outcome events.

Everything is local; only short text descriptions are stored (never images).
"""
import datetime
import hashlib
import json
import math
import random
import re
import time
from dataclasses import dataclass

DIM = 1024


def _stem(w):
    for suf in ("ing", "ed", "es", "s", "ly"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def _tokens(text):
    words = [_stem(w) for w in re.findall(r"[a-z]+", str(text).lower()) if len(w) >= 3]
    return words + [a + "_" + b for a, b in zip(words, words[1:])]


def _bucket(token):
    return int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % DIM


def _norm(vec):
    n = math.sqrt(sum(v * v for v in vec.values()))
    return {k: v / n for k, v in vec.items()} if n else {}


def _cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


@dataclass
class DwellSignal:
    cluster_id: int
    dwell_s: float
    median_s: float
    z: float
    surprise: float
    fit: float
    visit_start: float
    last_text: str
    should_nudge: bool
    reason: str


class SceneSituationModel:
    def __init__(self, memory, config=None, clock=time.time, rng=None, embedder=None):
        self.memory = memory
        c = (config or {}).get("scene", {}) if isinstance((config or {}).get("scene", {}), dict) else {}
        self.embedder = embedder      # optional text -> dense vector (e.g. Ollama nomic-embed-text)
        self.mode = "dense" if embedder else "hash"
        self.theta = float(c.get("dense_cluster_threshold", 0.72) if embedder else c.get("cluster_threshold", 0.30))
        self.max_clusters = int(c.get("max_clusters", 40))
        self.gap_s = float(c.get("gap_seconds", 240))
        self.min_visits = int(c.get("min_visits", 5))
        self.min_days = int(c.get("min_days", 3))
        self.dwell_floor_s = float(c.get("dwell_floor_seconds", 600))
        self.z_threshold = float(c.get("z_threshold", 1.5))
        self.refit_at = tuple(c.get("refit_at", (25, 60, 120, 250)))
        self.clock = clock
        self.rng = rng or random.Random()
        self._reset_runtime()
        with memory._db_lock:
            db = memory.db
            db.execute("CREATE TABLE IF NOT EXISTS scene_obs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, text TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS scene_clusters(id INTEGER PRIMARY KEY, centroid TEXT NOT NULL, n INTEGER NOT NULL, first_ts REAL NOT NULL, last_ts REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS scene_visits(id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id INTEGER NOT NULL, start_ts REAL NOT NULL, dwell_s REAL NOT NULL, hour_bucket INTEGER NOT NULL, day TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS scene_terms(bucket INTEGER PRIMARY KEY, df INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS scene_arms(cluster_id INTEGER NOT NULL, hour_bucket INTEGER NOT NULL, alpha REAL NOT NULL, beta REAL NOT NULL, updated_ts REAL NOT NULL, PRIMARY KEY(cluster_id,hour_bucket))")
            db.execute("CREATE TABLE IF NOT EXISTS scene_arm_done(nudge_id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE IF NOT EXISTS scene_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            old = db.execute("SELECT value FROM scene_meta WHERE key='mode'").fetchone()
            if old and old[0] != self.mode:
                # Vector spaces differ; learned clusters/visits cannot be mixed. Raw observations are kept.
                for t in ("scene_clusters", "scene_visits", "scene_terms", "scene_arms", "scene_arm_done"):
                    db.execute("DELETE FROM " + t)
            db.execute("INSERT OR REPLACE INTO scene_meta(key,value) VALUES('mode',?)", (self.mode,))
            db.commit()
        self._load()

    # ------------------------------------------------------------------ state
    def _reset_runtime(self):
        self.clusters = {}          # id -> [centroid, n]
        self.df = {}                # bucket -> doc freq
        self.n_docs = 0
        self.cur = None             # current confirmed cluster id
        self.cand = None
        self.cand_count = 0
        self.cand_first_ts = 0.0
        self.visit_start = 0.0
        self.last_obs_ts = 0.0
        self.last_text = ""
        self.nudged_visit = None
        self.n_obs = 0
        self.pending_change = None  # (cluster_id, text, ts, novel)
        self._lex_base = None       # fallback change detector (word set), used when embeddings are unavailable
        self._lex_diff = 0

    def _load(self):
        with self.memory._db_lock:
            db = self.memory.db
            for r in db.execute("SELECT id,centroid,n FROM scene_clusters"):
                self.clusters[int(r[0])] = [{int(k): v for k, v in json.loads(r[1]).items()}, int(r[2])]
            self.df = {int(r[0]): int(r[1]) for r in db.execute("SELECT bucket,df FROM scene_terms")}
            self.n_obs = int(db.execute("SELECT COUNT(*) FROM scene_obs").fetchone()[0])
        self.n_docs = self.n_obs

    def _vector(self, text, update_df=False):
        if self.mode == "dense":
            raw = self.embedder(text)            # may raise: the observation is then skipped, never mixed
            return _norm({i: float(v) for i, v in enumerate(raw)})
        toks = _tokens(text)
        if not toks:
            return {}
        counts = {}
        for t in toks:
            counts[_bucket(t)] = counts.get(_bucket(t), 0) + 1
        if update_df:
            self.n_docs += 1
            for b in counts:
                self.df[b] = self.df.get(b, 0) + 1
        vec = {b: (1 + math.log(c)) * (math.log((self.n_docs + 1) / (self.df.get(b, 0) + 1)) + 1.0) for b, c in counts.items()}
        return _norm(vec)

    # -------------------------------------------------------------- ingestion
    def observe(self, text, ts=None, _replay=False):
        """Feed one scene description. Safe to call from a worker thread."""
        text = " ".join(str(text or "").split())[:300]
        if not text:
            return None
        ts = self.clock() if ts is None else float(ts)
        if self.last_obs_ts and ts - self.last_obs_ts > self.gap_s:
            self._close_visit(self.last_obs_ts)
        try:
            vec = self._vector(text, update_df=not _replay)
        except Exception:
            if not _replay:
                self._lexical_change(text, ts)
            return None
        cid = self._assign(vec, ts)
        if cid is None:
            return None
        self.last_text, self.last_obs_ts = text, ts
        if self.cur is None:
            self.cur, self.visit_start, self.cand, self.cand_count = cid, ts, None, 0
            if not _replay: self.pending_change = (cid, text, ts, self.clusters[cid][1] <= 3)
        elif cid == self.cur:
            self.cand, self.cand_count = None, 0
        else:
            if self.cand == cid:
                self.cand_count += 1
            else:
                self.cand, self.cand_count, self.cand_first_ts = cid, 1, ts
            if self.cand_count >= 2:
                self._close_visit(self.cand_first_ts)
                self.cur, self.visit_start, self.cand, self.cand_count = cid, self.cand_first_ts, None, 0
                if not _replay: self.pending_change = (cid, text, ts, self.clusters[cid][1] <= 3)
        if not _replay:
            with self.memory._db_lock:
                self.memory.db.execute("INSERT INTO scene_obs(ts,text) VALUES(?,?)", (ts, text))
                self.memory.db.execute("DELETE FROM scene_obs WHERE id NOT IN (SELECT id FROM scene_obs ORDER BY id DESC LIMIT 3000)")
                self.memory.db.commit()
            self.n_obs += 1
            self._persist_terms()
            if self.mode == "hash" and self.n_obs in self.refit_at:
                self.refit()
        return cid

    def _lexical_change(self, text, ts):
        """Embedding model unavailable: still notice a new camera start or a clearly different scene."""
        words = {w for w in (_stem(x) for x in re.findall(r"[a-z]+", text.lower())) if len(w) >= 4}
        if self._lex_base is None:
            self._lex_base, self._lex_diff = words, 0
            self.pending_change = (0, text, ts, True)
            return
        union = self._lex_base | words
        sim = len(self._lex_base & words) / len(union) if union else 1.0
        self._lex_diff = self._lex_diff + 1 if sim < 0.2 else 0
        if self._lex_diff >= 2:
            self._lex_base, self._lex_diff = words, 0
            self.pending_change = (0, text, ts, False)

    def _assign(self, vec, ts):
        if not vec:
            return None
        best, best_s = None, -1.0
        for cid, (cen, _n) in self.clusters.items():
            s = _cos(vec, cen)
            if s > best_s:
                best, best_s = cid, s
        if best is not None and (best_s >= self.theta or len(self.clusters) >= self.max_clusters):
            cen, n = self.clusters[best]
            w = min(n, 50)
            merged = dict((k, v * w) for k, v in cen.items())
            for k, v in vec.items():
                merged[k] = merged.get(k, 0.0) + v
            self.clusters[best] = [_norm(merged), n + 1]
            self._save_cluster(best, ts)
            return best
        cid = (max(self.clusters) + 1) if self.clusters else 1
        self.clusters[cid] = [vec, 1]
        self._save_cluster(cid, ts, new=True)
        return cid

    def _save_cluster(self, cid, ts, new=False):
        cen, n = self.clusters[cid]
        with self.memory._db_lock:
            self.memory.db.execute(
                "INSERT INTO scene_clusters(id,centroid,n,first_ts,last_ts) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET centroid=excluded.centroid,n=excluded.n,last_ts=excluded.last_ts",
                (cid, json.dumps({str(k): round(v, 5) for k, v in cen.items()}), n, ts, ts))
            self.memory.db.commit()

    def _persist_terms(self):
        if self.n_obs % 10:
            return
        with self.memory._db_lock:
            self.memory.db.executemany("INSERT OR REPLACE INTO scene_terms(bucket,df) VALUES(?,?)", list(self.df.items()))
            self.memory.db.commit()

    def _close_visit(self, end_ts):
        if self.cur is not None and end_ts - self.visit_start >= 30:
            dt = datetime.datetime.fromtimestamp(self.visit_start)
            with self.memory._db_lock:
                self.memory.db.execute(
                    "INSERT INTO scene_visits(cluster_id,start_ts,dwell_s,hour_bucket,day) VALUES(?,?,?,?,?)",
                    (self.cur, self.visit_start, end_ts - self.visit_start, (dt.hour // 3) * 3, dt.date().isoformat()))
                self.memory.db.commit()
        self.cur, self.cand, self.cand_count = None, None, 0

    def end_session(self, ts=None):
        """Call when the camera stops so the open visit is recorded."""
        if self.last_obs_ts:
            self._close_visit(self.last_obs_ts)
        self.last_obs_ts = 0.0
        self._lex_base = None

    def refit(self):
        """Rebuild clusters and visits from stored observations with the now-better IDF statistics."""
        with self.memory._db_lock:
            rows = self.memory.db.execute("SELECT ts,text FROM scene_obs ORDER BY ts").fetchall()
            db = self.memory.db
            db.execute("DELETE FROM scene_clusters"); db.execute("DELETE FROM scene_visits"); db.execute("DELETE FROM scene_arms"); db.execute("DELETE FROM scene_arm_done")
            db.commit()
        n_obs = self.n_obs
        keep_df, keep_docs = dict(self.df), self.n_docs
        self._reset_runtime()
        self.df, self.n_docs, self.n_obs = keep_df, keep_docs, n_obs
        for ts, text in rows:
            self.observe(text, ts=float(ts), _replay=True)
        self._close_visit(self.last_obs_ts) if self.last_obs_ts else None
        self.last_obs_ts = 0.0

    # --------------------------------------------------------------- baseline
    def _visits(self, cid):
        with self.memory._db_lock:
            return [(float(r[0]), int(r[1]), str(r[2])) for r in self.memory.db.execute(
                "SELECT dwell_s,hour_bucket,day FROM scene_visits WHERE cluster_id=?", (cid,))]

    @staticmethod
    def _quantile(xs, q):
        xs = sorted(xs)
        if not xs:
            return 0.0
        pos = (len(xs) - 1) * q
        lo, hi = int(math.floor(pos)), int(math.ceil(pos))
        return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

    def _surprise(self, cid, bucket):
        with self.memory._db_lock:
            total = self.memory.db.execute("SELECT COUNT(*) FROM scene_visits WHERE hour_bucket=?", (bucket,)).fetchone()[0]
            mine = self.memory.db.execute("SELECT COUNT(*) FROM scene_visits WHERE hour_bucket=? AND cluster_id=?", (bucket, cid)).fetchone()[0]
        k = max(2, len(self.clusters))
        p = (mine + 0.5) / (total + 0.5 * k)
        return max(0.0, min(1.0, -math.log(p) / math.log(k)))

    def dwell_signal(self, now=None):
        now = self.clock() if now is None else float(now)
        if self.cur is None or not self.last_obs_ts or now - self.last_obs_ts > self.gap_s:
            return None
        dwell = self.last_obs_ts - self.visit_start
        dt = datetime.datetime.fromtimestamp(self.visit_start)
        bucket = (dt.hour // 3) * 3
        visits = self._visits(self.cur)
        days = {v[2] for v in visits}
        base = dict(cluster_id=self.cur, dwell_s=dwell, visit_start=self.visit_start, last_text=self.last_text)
        if len(visits) < self.min_visits or len(days) < self.min_days:
            return DwellSignal(median_s=0, z=0, surprise=0, fit=0, should_nudge=False,
                               reason="Still learning your routine for this situation.", **base)
        same = [v[0] for v in visits if v[1] == bucket]
        durs = same if len(same) >= 3 else [v[0] for v in visits]
        med = self._quantile(durs, 0.5)
        mad = self._quantile([abs(d - med) for d in durs], 0.5)
        p80 = self._quantile(durs, 0.8)
        z = (dwell - med) / (1.4826 * mad + 0.25 * med + 60.0)
        threshold = max(self.dwell_floor_s, p80)
        surprise = self._surprise(self.cur, bucket)
        urgency = max(0.0, min(1.0, 0.5 + 0.25 * (z - self.z_threshold)))
        fit = urgency * (0.7 + 0.3 * surprise)
        ok = (self.mode == "dense" and dwell >= threshold and dwell >= 1.5 * med
              and z >= self.z_threshold and self.nudged_visit != self.visit_start)
        reason = (f"You have been in this same situation for about {int(dwell // 60)} min; your usual is about "
                  f"{int(med // 60)} min (learned from {len(visits)} earlier visits).")
        return DwellSignal(median_s=med, z=z, surprise=surprise, fit=fit, should_nudge=ok, reason=reason, **base)

    def change_signal(self, now=None, max_age=180):
        """A recent confirmed change of situation (or camera start) not yet commented on."""
        now = self.clock() if now is None else float(now)
        pc = self.pending_change
        if not pc or now - pc[2] > max_age:
            return None
        return {"cluster_id": pc[0], "text": pc[1], "ts": pc[2], "novel": pc[3]}

    def change_message(self, sig):
        desc = str(sig["text"]).rstrip(". ")
        desc = desc[:140] + ("…" if len(desc) > 140 else "")
        lead = "This looks new to me" if sig["novel"] else "I noticed a change"
        return f"{lead}: the camera sees “{desc}”. Is that right, and is there anything I can help with?"

    def consume_change(self):
        self.pending_change = None

    def message(self, sig):
        desc = sig.last_text.rstrip(". ")
        desc = desc[:110] + ("…" if len(desc) > 110 else "")
        return (f"Hey, the camera last described this as “{desc}”. You have been in this same situation for about "
                f"{max(1, int(sig.dwell_s // 60))} minutes, longer than your usual ~{max(1, int(sig.median_s // 60))}. "
                "Want a quick break, or is everything fine?")

    def mark_nudged(self, sig):
        self.nudged_visit = sig.visit_start

    # ------------------------------------------------------- learning to nudge
    def sample_acceptance(self, cluster_id, hour, floor=0.15):
        """Thompson sample of the probability that a nudge here/now is welcome."""
        bucket = (int(hour) // 3) * 3
        with self.memory._db_lock:
            r = self.memory.db.execute("SELECT alpha,beta FROM scene_arms WHERE cluster_id=? AND hour_bucket=?", (cluster_id, bucket)).fetchone()
        a, b = (float(r[0]), float(r[1])) if r else (1.0, 1.0)
        return max(floor, self.rng.betavariate(a, b))

    def sync_feedback(self, now=None):
        """Turn outcomes of earlier scene nudges into Beta updates (idempotent)."""
        now = self.clock() if now is None else float(now)
        with self.memory._db_lock:
            db = self.memory.db
            cols = {r[1] for r in db.execute("PRAGMA table_info(companion_events)")}
            if "nudge_id" not in cols:
                return 0
            shown = db.execute(
                "SELECT nudge_id,ts,meta FROM companion_events WHERE kind='nudge_shown' AND trigger IN ('scene_dwell','scene_change') AND nudge_id IS NOT NULL "
                "AND nudge_id NOT IN (SELECT nudge_id FROM scene_arm_done)").fetchall()
            done = 0
            for nid, ts, meta in shown:
                outcome = db.execute(
                    "SELECT kind FROM companion_events WHERE nudge_id=? AND kind IN ('nudge_replied','nudge_negative','nudge_ignored','nudge_late_reply','nudge_unseen') "
                    "ORDER BY ts LIMIT 1", (nid,)).fetchone()
                if not outcome:
                    continue
                try:
                    cid = int(json.loads(meta or "{}").get("cluster_id"))
                except (TypeError, ValueError):
                    cid = None
                kind = outcome[0]
                if cid is not None and kind != "nudge_unseen":
                    bucket = (datetime.datetime.fromtimestamp(float(ts)).hour // 3) * 3
                    r = db.execute("SELECT alpha,beta FROM scene_arms WHERE cluster_id=? AND hour_bucket=?", (cid, bucket)).fetchone()
                    a, b = (float(r[0]), float(r[1])) if r else (1.0, 1.0)
                    if kind == "nudge_replied": a += 1.0
                    elif kind == "nudge_late_reply": a += 0.3
                    elif kind == "nudge_negative": b += 2.0
                    elif kind == "nudge_ignored": b += 0.3
                    db.execute("INSERT OR REPLACE INTO scene_arms(cluster_id,hour_bucket,alpha,beta,updated_ts) VALUES(?,?,?,?,?)", (cid, bucket, a, b, now))
                db.execute("INSERT OR IGNORE INTO scene_arm_done(nudge_id) VALUES(?)", (nid,))
                done += 1
            db.commit()
        return done

    def delete_all(self):
        with self.memory._db_lock:
            for t in ("scene_obs", "scene_clusters", "scene_visits", "scene_terms", "scene_arms", "scene_arm_done"):
                self.memory.db.execute("DELETE FROM " + t)
            self.memory.db.commit()
        self._reset_runtime()
