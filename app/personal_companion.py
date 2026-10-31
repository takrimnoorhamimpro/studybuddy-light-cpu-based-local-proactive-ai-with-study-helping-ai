"""Consent-first personal knowledge graph and persistent conversation follow-ups.

Uses the existing per-user SQLite database; no second LLM or cloud service.
Facts are only saved after explicit confirmation. Inferences remain candidates.
"""
import json, re, time, uuid
from companion.migrations import migrate

SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_nodes(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'candidate', evidence TEXT NOT NULL DEFAULT '',
 consented INTEGER NOT NULL DEFAULT 0, created_ts REAL NOT NULL, updated_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS personal_edges(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
 relation TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '', created_ts REAL NOT NULL,
 FOREIGN KEY(source_id) REFERENCES personal_nodes(id) ON DELETE CASCADE,
 FOREIGN KEY(target_id) REFERENCES personal_nodes(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS companion_followups(
 id TEXT PRIMARY KEY, due_ts REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', created_ts REAL NOT NULL, delivered_ts REAL);
CREATE TABLE IF NOT EXISTS companion_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

class PersonalCompanion:
    def __init__(self, memory):
        self.memory = memory
        migrate(memory)
        with memory._db_lock:
            memory.db.executescript(SCHEMA)
            memory.db.commit()
        self.expire_low_quality_legacy_moments()

    def expire_low_quality_legacy_moments(self):
        """Hide filler episodes created by the old short-answer extractor."""
        with self.memory._db_lock:
            rows = self.memory.db.execute(
                "SELECT id,label,evidence FROM personal_nodes WHERE source='conversation' "
                "AND kind IN ('episode','moment','event') AND status IN ('confirmed','candidate') "
                "AND label LIKE 'The user recently shared:%'"
            ).fetchall()
            expired = []
            for row in rows:
                evidence = " ".join(str(row["evidence"] or "").split()).casefold()
                words = re.findall(r"[a-z0-9']+", evidence)
                meta_talk = bool(re.search(r"\b(?:chat window|guess later|the chatbot|you can guess|window title)\b", evidence))
                generic = bool(re.search(r"\b(?:nothing new|nothing much|not much|no but i have learned something|it's been great|it has been great|its been great|same as usual)\b", evidence))
                meaningful_event = bool(re.search(
                    r"(?i)\b(?:important to me|proud of|milestone|first time|achievement|changed my life|"
                    r"big moment|never forget|unforgettable|dream come true|passed.{0,40}(?:exam|test|degree|bachelor)|"
                    r"graduated|won.{0,40}(?:competition|award|medal|prize)|scholarship|promoted|married)\b",
                    evidence))
                # Legacy episode labels such as "The user recently shared: ..." were
                # produced from casual replies. Keep only entries with evidence of a
                # genuinely meaningful event; expired nodes remain recoverable in the DB.
                if len(words) < 8 or meta_talk or generic or not meaningful_event:
                    expired.append(str(row["id"]))
            if not expired:
                return 0
            marks = ",".join("?" for _ in expired)
            self.memory.db.execute(
                f"UPDATE personal_nodes SET status='expired',consented=0,updated_ts=? WHERE id IN ({marks})",
                [time.time()] + expired)
            self.memory.db.execute(f"DELETE FROM candidate_queue WHERE node_id IN ({marks})", expired)
            self.memory.db.commit()
            return len(expired)

    def setting(self, key, default=None):
        with self.memory._db_lock:
            r=self.memory.db.execute('SELECT value FROM companion_settings WHERE key=?',(key,)).fetchone()
            return r['value'] if r else default

    def set_setting(self, key, value):
        with self.memory._db_lock:
            self.memory.db.execute('INSERT OR REPLACE INTO companion_settings VALUES (?,?)',(key,str(value)))
            self.memory.db.commit()

    def add_fact(self, label, kind='interest', evidence='', confirmed=False, consented=False, source='manual', expires_ts=None, sensitivity='normal', importance=0.5):
        label=(label or '').strip()[:240]
        if not label: raise ValueError('A memory label is required')
        if confirmed and not consented: raise ValueError('Confirmed personal memories require explicit consent')
        source = str(source or "manual").strip().lower()
        if source not in {"manual", "conversation", "onboarding"}: source = "manual"
        sensitivity = str(sensitivity or "sensitive").strip().lower()
        if sensitivity not in {"normal", "sensitive"}: sensitivity = "sensitive"
        try: importance = max(0.0, min(1.0, float(importance)))
        except (TypeError, ValueError): importance = 0.5
        now=time.time(); nid=uuid.uuid4().hex
        with self.memory._db_lock:
            old=self.memory.db.execute('SELECT id FROM personal_nodes WHERE label=? COLLATE NOCASE AND kind=?',(label,kind)).fetchone()
            if old:
                self.memory.db.execute('UPDATE personal_nodes SET status=?,evidence=?,consented=?,updated_ts=?,source=?,expires_ts=?,sensitivity=?,importance=? WHERE id=?',('confirmed' if confirmed else 'candidate',evidence[:1000],int(consented),now,str(source),expires_ts,str(sensitivity),float(importance),old['id']))
                nid=old['id']
            else:
                self.memory.db.execute('INSERT INTO personal_nodes (id,label,kind,status,evidence,consented,created_ts,updated_ts,source,expires_ts,sensitivity,importance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(nid,label,kind,'confirmed' if confirmed else 'candidate',evidence[:1000],int(consented),now,now,str(source),expires_ts,str(sensitivity),float(importance)))
            if confirmed and consented:
                self.memory.db.execute("INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)", (now, "facts", 1, "confirmed_fact_added"))
            self.memory.db.commit()
        return nid

    def update_fact(self, node_id, label, kind):
        """Edit one My World node in place without changing its consent status."""
        label = " ".join(str(label or "").split()).strip()[:240]
        kind = str(kind or "").strip().lower()[:40]
        if not label:
            raise ValueError("A memory label is required")
        allowed_kinds = {
            "profile", "area", "study_work", "education", "occupation", "routine", "schedule", "habit",
            "location", "place", "interest", "hobby", "goal", "plan", "aim", "project", "deadline",
            "achievement", "value", "meaning", "people", "relationship", "family", "friend", "good_moment",
            "hard_moment", "challenge", "setback", "preference", "style", "support_style", "episode", "moment", "event",
        }
        now = time.time()
        with self.memory._db_lock:
            db = self.memory.db
            existing = db.execute("SELECT id,kind,consented FROM personal_nodes WHERE id=?", (str(node_id),)).fetchone()
            if not existing:
                return False
            if kind not in allowed_kinds and kind != str(existing["kind"] or "").lower():
                raise ValueError("Choose a supported My World section")
            duplicate = db.execute(
                "SELECT 1 FROM personal_nodes WHERE label=? COLLATE NOCASE AND kind=? AND id<>? LIMIT 1",
                (label, kind, str(node_id)),
            ).fetchone()
            if duplicate:
                raise ValueError("Another My World detail already has that label and section")
            db.execute(
                "UPDATE personal_nodes SET label=?,kind=?,evidence=?,source='manual',updated_ts=? WHERE id=?",
                (label, kind, "Manually edited in My World by the user.", now, str(node_id)),
            )
            db.execute(
                "INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)",
                (now, "facts", int(bool(existing["consented"])), "node_edited_in_my_world"),
            )
            db.commit()
            return True

    def confirm_fact(self, node_id):
        now=time.time()
        with self.memory._db_lock:
            cur=self.memory.db.execute("UPDATE personal_nodes SET status='confirmed',consented=1,updated_ts=? WHERE id=? AND status='candidate'",(now,node_id))
            if cur.rowcount:
                self.memory.db.execute('DELETE FROM candidate_queue WHERE node_id=?',(node_id,))
                self.memory.db.execute("INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)", (now, "facts", 1, "candidate_fact_confirmed"))
            self.memory.db.commit(); return cur.rowcount == 1

    def forget_fact(self, node_id):
        with self.memory._db_lock:
            db=self.memory.db
            loop_ids=[str(r[0]) for r in db.execute("SELECT id FROM open_loops WHERE node_id=?",(node_id,)).fetchall()]
            db.execute('DELETE FROM candidate_queue WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM personal_edges WHERE source_id=? OR target_id=?',(node_id,node_id))
            db.execute("DELETE FROM open_loops WHERE node_id=?",(node_id,))
            if loop_ids:
                for loop_id in loop_ids: db.execute("DELETE FROM companion_events WHERE meta LIKE ?",('%'+loop_id+'%',))
            db.execute("DELETE FROM companion_events WHERE meta LIKE ? OR meta LIKE ?",('%"node_id":"'+str(node_id)+'"%','%"node_id": "'+str(node_id)+'"%'))
            cur=db.execute('DELETE FROM personal_nodes WHERE id=?',(node_id,))
            if cur.rowcount:
                db.execute("INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)",(time.time(),"facts",0,"node_forgotten"))
            db.commit(); return cur.rowcount == 1

    def list_nodes(self, include_candidates=True):
        sql='SELECT * FROM personal_nodes'
        if not include_candidates: sql += " WHERE status='confirmed'"
        sql += ' ORDER BY updated_ts DESC'
        with self.memory._db_lock: return [dict(r) for r in self.memory.db.execute(sql).fetchall()]

    def add_relation(self, source_id, target_id, relation, evidence=''):
        now=time.time(); eid=uuid.uuid4().hex
        with self.memory._db_lock:
            self.memory.db.execute('INSERT INTO personal_edges VALUES (?,?,?,?,?,?)',(eid,source_id,target_id,relation[:100],evidence[:1000],now)); self.memory.db.commit()
        return eid

    def list_edges(self):
        with self.memory._db_lock: return [dict(r) for r in self.memory.db.execute('SELECT * FROM personal_edges ORDER BY created_ts DESC').fetchall()]

    def queue_candidate(self, node_id, now=None, max_queue=5):
        now = time.time() if now is None else float(now)
        with self.memory._db_lock:
            exists = self.memory.db.execute("SELECT status FROM personal_nodes WHERE id=?", (node_id,)).fetchone()
            if not exists or exists["status"] != "candidate": return False
            queued = self.memory.db.execute("SELECT COUNT(*) FROM candidate_queue q JOIN personal_nodes n ON n.id=q.node_id WHERE n.status='candidate'").fetchone()[0]
            already = self.memory.db.execute("SELECT 1 FROM candidate_queue WHERE node_id=?", (node_id,)).fetchone()
            if already: return True
            if int(queued) >= int(max_queue): return False
            self.memory.db.execute("INSERT INTO candidate_queue(node_id,queued_ts,deferrals,last_prompted_ts) VALUES(?,?,0,NULL)", (node_id, now))
            self.memory.db.commit(); return True

    def queued_candidates(self, limit=5):
        with self.memory._db_lock:
            rows=self.memory.db.execute("SELECT n.*,q.queued_ts,q.deferrals,q.last_prompted_ts FROM candidate_queue q JOIN personal_nodes n ON n.id=q.node_id WHERE n.status='candidate' ORDER BY q.queued_ts,n.created_ts LIMIT ?", (max(1,min(int(limit),5)),)).fetchall()
            return [dict(r) for r in rows]

    def mark_candidates_prompted(self, node_ids, now=None):
        now=time.time() if now is None else float(now)
        ids=[str(x) for x in node_ids]
        if not ids: return 0
        with self.memory._db_lock:
            marks=','.join('?' for _ in ids)
            cur=self.memory.db.execute(f"UPDATE candidate_queue SET last_prompted_ts=? WHERE node_id IN ({marks})",[now]+ids)
            self.memory.db.commit(); return cur.rowcount

    def reject_candidates(self, node_ids, now=None):
        now=time.time() if now is None else float(now); ids=[str(x) for x in node_ids]
        if not ids: return 0
        with self.memory._db_lock:
            marks=','.join('?' for _ in ids)
            cur=self.memory.db.execute(f"UPDATE personal_nodes SET status='rejected',consented=0,updated_ts=? WHERE id IN ({marks})",[now]+ids)
            self.memory.db.execute(f"DELETE FROM candidate_queue WHERE node_id IN ({marks})",ids)
            self.memory.db.execute("INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)",(now,'facts',0,'candidate_batch_declined'))
            self.memory.db.commit(); return cur.rowcount

    def defer_candidates(self, node_ids, now=None):
        now=time.time() if now is None else float(now); ids=[str(x) for x in node_ids]
        if not ids: return 0
        with self.memory._db_lock:
            marks=','.join('?' for _ in ids)
            self.memory.db.execute(f"UPDATE candidate_queue SET deferrals=deferrals+1 WHERE node_id IN ({marks})",ids)
            self.memory.db.execute(f"UPDATE personal_nodes SET status='expired',updated_ts=? WHERE id IN (SELECT node_id FROM candidate_queue WHERE deferrals>=2 AND node_id IN ({marks}))",[now]+ids)
            self.memory.db.execute(f"DELETE FROM candidate_queue WHERE node_id IN (SELECT node_id FROM personal_nodes WHERE status='expired' AND id IN ({marks}))",ids)
            self.memory.db.commit(); return len(ids)

    def expire_candidates(self, now=None):
        now=time.time() if now is None else float(now)
        with self.memory._db_lock:
            self.memory.db.execute("UPDATE personal_nodes SET status='expired',updated_ts=? WHERE status='candidate' AND expires_ts IS NOT NULL AND expires_ts<=?",(now,now))
            cur=self.memory.db.execute("DELETE FROM candidate_queue WHERE node_id IN (SELECT id FROM personal_nodes WHERE status='expired')")
            self.memory.db.commit(); return cur.rowcount

    def set_facts_tier(self, enabled):
        """Atomically persist the facts-consent switch and its audit record."""
        now=time.time(); value="1" if bool(enabled) else "0"
        with self.memory._db_lock:
            db=self.memory.db
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT OR REPLACE INTO companion_settings(key,value) VALUES('tier_facts',?)",(value,))
                db.execute("INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)",(now,"facts",int(bool(enabled)),"facts_tier_toggle"))
                db.commit()
            except Exception:
                db.rollback(); raise
        return bool(enabled)

    def add_consent_ledger(self, scope, granted, note=''):
        with self.memory._db_lock:
            self.memory.db.execute("INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)",(time.time(),str(scope),int(bool(granted)),str(note)[:120]))
            self.memory.db.commit()

    def schedule_followup(self, due_ts, kind='onboarding', payload=None):
        """Create or update one pending follow-up per kind to avoid duplicate prompts."""
        encoded=json.dumps(payload or {},ensure_ascii=False)
        with self.memory._db_lock:
            old=self.memory.db.execute(
                "SELECT id FROM companion_followups WHERE kind=? AND status='pending' ORDER BY created_ts LIMIT 1",
                (kind,)).fetchone()
            if old:
                self.memory.db.execute(
                    "UPDATE companion_followups SET due_ts=?,payload=?,created_ts=? WHERE id=?",
                    (float(due_ts),encoded,time.time(),old['id']))
                self.memory.db.commit()
                return old['id']
            fid=uuid.uuid4().hex
            self.memory.db.execute('INSERT INTO companion_followups (id,due_ts,kind,payload,status,created_ts,delivered_ts,claimed_ts) VALUES (?,?,?,?,?,?,NULL,NULL)',
                (fid,float(due_ts),kind,encoded,'pending',time.time()))
            self.memory.db.commit()
            return fid

    def claim_due_followups(self, now=None, kinds=('onboarding',), claim_timeout=120):
        """Atomically claim due supported follow-ups; callers must ack after display."""
        now=time.time() if now is None else float(now)
        kinds=tuple(kinds or ())
        if not kinds: return []
        with self.memory._db_lock:
            self.memory.db.execute(
                "UPDATE companion_followups SET status='pending',claimed_ts=NULL WHERE status='claimed' AND claimed_ts<=?",
                (now-max(1,int(claim_timeout)),))
            placeholders=','.join('?' for _ in kinds)
            rows=self.memory.db.execute(
                f"SELECT * FROM companion_followups WHERE status='pending' AND due_ts<=? AND kind IN ({placeholders}) ORDER BY due_ts",
                (now,*kinds)).fetchall()
            for row in rows:
                self.memory.db.execute("UPDATE companion_followups SET status='claimed',claimed_ts=? WHERE id=? AND status='pending'",(now,row['id']))
            self.memory.db.commit()
            return [dict(r) for r in rows]

    def acknowledge_followup(self, followup_id, now=None):
        now=time.time() if now is None else float(now)
        with self.memory._db_lock:
            cur=self.memory.db.execute("UPDATE companion_followups SET status='delivered',delivered_ts=? WHERE id=? AND status='claimed'",(now,followup_id))
            self.memory.db.commit(); return cur.rowcount==1

    def release_followup(self, followup_id):
        with self.memory._db_lock:
            cur=self.memory.db.execute("UPDATE companion_followups SET status='pending',claimed_ts=NULL WHERE id=? AND status='claimed'",(followup_id,))
            self.memory.db.commit(); return cur.rowcount==1

    def due_followups(self, now=None):
        """Backward-compatible one-shot helper; production UI uses claim/ack."""
        rows=self.claim_due_followups(now=now, kinds=('onboarding',))
        for row in rows: self.acknowledge_followup(row['id'], now=now)
        return rows

    def cancel_followup(self, followup_id):
        with self.memory._db_lock:
            cur=self.memory.db.execute("UPDATE companion_followups SET status='cancelled' WHERE id=? AND status IN ('pending','claimed')",(followup_id,)); self.memory.db.commit(); return cur.rowcount==1
