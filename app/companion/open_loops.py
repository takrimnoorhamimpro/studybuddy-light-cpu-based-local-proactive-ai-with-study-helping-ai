"""Deadline/commitment loop tracker backed by the existing SQLite database."""
from dataclasses import dataclass
import datetime, hashlib, re, time, uuid

@dataclass
class OpenLoop:
    id:str; title:str; kind:str; due_ts:float|None; status:str; source:str; node_id:str|None; ref_id:str|None; step:int; snoozed_until:float|None
@dataclass
class LoopNudge:
    loop_id:str; title:str; step:int; step_index:int; due_ts:float; reason:str; text:str

class OpenLoopTracker:
    def __init__(self, memory, personal_companion, config=None, clock=time.time, rhythm=None):
        self.memory=memory; self.pc=personal_companion; self.config=config or {}; self.clock=clock; self.rhythm=rhythm
    def _db(self): return self.memory.db
    def sync(self):
        now=self.clock(); db=self._db()
        with self.memory._db_lock:
            rows=db.execute("SELECT id,label,kind,expires_ts FROM personal_nodes WHERE status='confirmed' AND consented=1 AND kind IN ('deadline','area') AND COALESCE(sensitivity,'normal')='normal'").fetchall()
            for r in rows:
                due=None
                # Extract only explicit date phrases retained in the neutral label.
                from companion.extractor import parse_date
                dt=parse_date(str(r['label']), now)
                if dt: due=dt.timestamp()
                if due is None and r['expires_ts'] is not None: due=float(r['expires_ts'])-86400
                if due is None: continue
                raw_title=str(r['label'] or '')
                raw_title=re.sub(r"(?i)^the user(?:'s)?\s+",'',raw_title).rstrip('. ')
                raw_title=re.sub(r"(?i)^has\s+(?:an?|the)\s+",'',raw_title)
                raw_title=re.sub(r"(?i)^is\s+preparing\s+for\s+",'',raw_title)
                raw_title=re.split(r"(?i)\s+is\s+(?:due|on|by)\s+",raw_title,maxsplit=1)[0].strip()
                raw_title=re.split(r"(?i)\s+(?:on|next|this)\s+(?:tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2})",raw_title,maxsplit=1)[0].strip()
                raw_title=re.sub(r"(?i)^(?:preparing\s+for|studying)\s+",'',raw_title)
                title=raw_title[:100] or 'your deadline'
                db.execute("INSERT OR IGNORE INTO open_loops(id,title,kind,due_ts,status,source,node_id,created_ts,last_surfaced_ts,surfaced_count,step,snoozed_until,ref_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(uuid.uuid5(uuid.NAMESPACE_URL,'node:'+r['id']).hex,title,r['kind'],due,'open','node',r['id'],now,None,0,0,None,None))
                db.execute("UPDATE open_loops SET title=?,due_ts=? WHERE node_id=? AND status='open'",(title,due,r['id']))
            # Node-backed loops cease to exist as soon as the source node is no longer confirmed.
            db.execute("DELETE FROM open_loops WHERE source='node' AND node_id NOT IN (SELECT id FROM personal_nodes WHERE status='confirmed' AND consented=1)")
            # Existing tasks are read-only sources; never create loops from reminders, which already notify.
            try: tasks=db.execute("SELECT id,title,due_ts FROM tasks WHERE due_ts IS NOT NULL AND status='open'").fetchall()
            except Exception: tasks=[]
            for t in tasks:
                rid=str(t['id']); lid=uuid.uuid5(uuid.NAMESPACE_URL,'task:'+rid).hex
                db.execute("INSERT OR IGNORE INTO open_loops(id,title,kind,due_ts,status,source,node_id,created_ts,last_surfaced_ts,surfaced_count,step,snoozed_until,ref_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(lid,str(t['title'])[:120],'task',float(t['due_ts']),'open','task',None,now,None,0,0,None,rid))
            # Remove loops for deleted tasks; preserve historical done/dropped loops.
            live={str(t['id']) for t in tasks}
            for row in db.execute("SELECT id,ref_id FROM open_loops WHERE source='task' AND status='open'").fetchall():
                if row['ref_id'] not in live: db.execute("DELETE FROM open_loops WHERE id=?",(row['id'],))
            db.execute("UPDATE open_loops SET status='open',snoozed_until=NULL WHERE status='snoozed' AND snoozed_until IS NOT NULL AND snoozed_until<=?",(now,))
            db.execute("UPDATE open_loops SET status='expired' WHERE status IN ('open','snoozed') AND due_ts IS NOT NULL AND due_ts<?",(now-86400,))
            db.commit()
    def list_open(self):
        with self.memory._db_lock:
            return [OpenLoop(**dict(r)) for r in self._db().execute("SELECT id,title,kind,due_ts,status,source,node_id,ref_id,step,snoozed_until FROM open_loops WHERE status='open'").fetchall()]
    def due_candidates(self, now=None):
        now=self.clock() if now is None else float(now); self.sync(); out=[]
        days=self.config.get('loop_escalation_days',[3,1,0]); days=sorted({int(x) for x in days if int(x)>=0},reverse=True)
        for loop in self.list_open():
            if loop.due_ts is None or loop.snoozed_until and loop.snoozed_until>now: continue
            days_left=(datetime.datetime.fromtimestamp(loop.due_ts).date()-datetime.datetime.fromtimestamp(now).date()).days
            if days_left<0: continue
            for idx,day in enumerate(days):
                if days_left==day and loop.step<=idx:
                    if day==0:
                        due_local=datetime.datetime.fromtimestamp(loop.due_ts)
                        if loop.source=='task':
                            with self.memory._db_lock:
                                reminder=self._db().execute("SELECT 1 FROM reminders WHERE text LIKE ? AND ABS(remind_ts-?)<180 LIMIT 1",("%"+loop.title+"%",loop.due_ts)).fetchone()
                            if reminder: break  # Explicit due reminder already covers the due moment.
                        target_hour=9
                        if self.rhythm and self.rhythm.enabled():
                            profile=self.rhythm.profile(now)
                            if profile.confidence>0:
                                target_hour=(profile.weekend_start if due_local.weekday()>=5 else profile.weekday_start)
                                if target_hour is None: target_hour=9
                        else:
                            try: target_hour=int(str(self.config.get('loop_morning_default','09:00')).split(':',1)[0])
                            except Exception: target_hour=9
                        current=datetime.datetime.fromtimestamp(now)
                        if current.date()!=due_local.date() or current.hour<target_hour or current.hour>target_hour+1: break
                    # Missed windows are skipped silently; only exact escalation dates can surface.
                    textday='today' if day==0 else f"in {day} day{'s' if day!=1 else ''}"
                    out.append(LoopNudge(loop.id,loop.title,day,idx,loop.due_ts,f"Your deadline '{loop.title}' is {textday} (step: {day} days)",''))
                    break
        return out[:1]
    def mark_surfaced(self, loop_id, step, now=None):
        now=self.clock() if now is None else float(now)
        with self.memory._db_lock:
            self._db().execute("UPDATE open_loops SET step=?,last_surfaced_ts=?,surfaced_count=surfaced_count+1 WHERE id=? AND status='open'",(int(step)+1,now,loop_id)); self._db().commit()
    def snooze_next(self, loop_id, now=None):
        now=self.clock() if now is None else float(now)
        with self.memory._db_lock:
            row=self._db().execute("SELECT due_ts,step FROM open_loops WHERE id=? AND status='open'",(loop_id,)).fetchone()
        if not row or row['due_ts'] is None: return self.mark(loop_id,'snoozed',until=now+86400)
        days=sorted({int(x) for x in self.config.get('loop_escalation_days',[3,1,0]) if int(x)>=0},reverse=True)
        step=max(0,int(row['step'] or 0)); next_days=days[step] if step<len(days) else None
        if next_days is None: until=now+86400
        elif next_days==0:
            due=datetime.datetime.fromtimestamp(float(row['due_ts'])); until=due.replace(hour=9,minute=0,second=0,microsecond=0).timestamp()
        else: until=float(row['due_ts'])-next_days*86400
        if until<=now: until=now+60
        return self.mark(loop_id,'snoozed',until=until)

    def mark(self, loop_id, status, until=None):
        if status=='snoozed':
            with self.memory._db_lock: self._db().execute("UPDATE open_loops SET status='snoozed',snoozed_until=?,step=MAX(step,1) WHERE id=? AND status IN ('open','snoozed')",(float(until if until is not None else self.clock()+86400),loop_id)); self._db().commit(); return
        if status not in ('done','dropped'): raise ValueError('status must be done, dropped, or snoozed')
        with self.memory._db_lock: self._db().execute("UPDATE open_loops SET status=? WHERE id=? AND status='open'",(status,loop_id)); self._db().commit()
