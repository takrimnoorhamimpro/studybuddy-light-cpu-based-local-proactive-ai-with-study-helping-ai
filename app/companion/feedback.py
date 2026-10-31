"""Local nudge outcome logging and Bayesian trigger acceptance estimates."""
import json, time, datetime
class FeedbackLogger:
 def __init__(self,memory,config=None,clock=time.time): self.memory=memory; self.config=config or {}; self.clock=clock
 def event(self,kind,trigger=None,reason='',nudge_id=None,meta=None,now=None):
  now=self.clock() if now is None else float(now)
  payload=dict(meta or {}); payload['nudge_id']=nudge_id
  with self.memory._db_lock:
   cols={r[1] for r in self.memory.db.execute('PRAGMA table_info(companion_events)')}
   if 'nudge_id' in cols: self.memory.db.execute('INSERT INTO companion_events(ts,kind,trigger,reason,meta,nudge_id) VALUES(?,?,?,?,?,?)',(now,kind,trigger,reason,json.dumps(payload,separators=(',',':')),nudge_id))
   else: self.memory.db.execute('INSERT INTO companion_events(ts,kind,trigger,reason,meta) VALUES(?,?,?,?,?)',(now,kind,trigger,reason,json.dumps(payload,separators=(',',':'))))
   self.memory.db.commit()
  return now
 def acceptance(self,trigger,hour,now=None):
  now=self.clock() if now is None else float(now); bucket=(int(hour)//3)*3
  with self.memory._db_lock:
   row=self.memory.db.execute('SELECT alpha,beta,updated_ts FROM trigger_stats WHERE trigger=? AND hour_bucket=?',(trigger,bucket)).fetchone()
  if not row: return max(float(self.config.get('acceptance_floor',.15)),.5)
  age=max(0,now-float(row['updated_ts'])); decay=.98**(age/86400)
  a=1+(float(row['alpha'])-1)*decay; b=1+(float(row['beta'])-1)*decay
  return max(float(self.config.get('acceptance_floor',.15)),a/(a+b))
 def update(self,trigger,hour,success=False,negative=False,weight=1.0,now=None):
  now=self.clock() if now is None else float(now); bucket=(int(hour)//3)*3
  with self.memory._db_lock:
   row=self.memory.db.execute('SELECT alpha,beta,updated_ts FROM trigger_stats WHERE trigger=? AND hour_bucket=?',(trigger,bucket)).fetchone()
   a=b=1.0
   if row:
    decay=.98**(max(0,now-float(row['updated_ts']))/86400); a=1+(float(row['alpha'])-1)*decay; b=1+(float(row['beta'])-1)*decay
   if success: a+=float(weight)
   else: b+=float(weight)
   self.memory.db.execute('INSERT OR REPLACE INTO trigger_stats(trigger,hour_bucket,alpha,beta,updated_ts) VALUES(?,?,?,?,?)',(trigger,bucket,a,b,now)); self.memory.db.commit()
   return a/(a+b)

 def evaluate_pending(self, now=None, active=False):
  """Resolve the latest shown nudge without treating absence as rejection."""
  now=self.clock() if now is None else float(now)
  with self.memory._db_lock:
   row=self.memory.db.execute("SELECT nudge_id,trigger,ts,reason FROM companion_events WHERE kind='nudge_shown' ORDER BY ts DESC LIMIT 1").fetchone()
   if not row: return None
   seen=self.memory.db.execute("SELECT 1 FROM companion_events WHERE nudge_id=? AND kind IN ('nudge_replied','nudge_negative','nudge_ignored','nudge_unseen','nudge_late_reply','nudge_muted') LIMIT 1",(row['nudge_id'],)).fetchone()
  if seen: return None
  age=now-float(row['ts']); ignore_minutes=max(1,int(self.config.get('ignore_window_minutes',15)))
  if age<ignore_minutes*60: return None
  if row['trigger']=='title_clue':
   # Silence after a window observation is ambiguous: the user may simply be
   # focused on that window. Record resolution without lowering future curiosity.
   self.event('nudge_unseen',row['trigger'],'No reply; title check-ins remain enabled.',row['nudge_id'],{},now)
   return 'unseen'
  hour=datetime.datetime.fromtimestamp(float(row['ts'])).hour
  if active:
   self.event('nudge_ignored',row['trigger'],'No reply within the active 15-minute observation window.',row['nudge_id'],{},now)
   self.update(row['trigger'],hour,success=False,weight=.3,now=now)
   with self.memory._db_lock:
    ignored=self.memory.db.execute("SELECT COUNT(*) FROM companion_events WHERE kind='nudge_ignored' AND trigger=? AND ts>=?",(row['trigger'],now-7*86400)).fetchone()[0]
    if ignored>=2:
     self.memory.db.execute("INSERT OR REPLACE INTO companion_settings(key,value) VALUES(?,?)",('trigger_halved_until_'+str(row['trigger']),str(now+7*86400)))
     self.memory.db.commit()
   return 'ignored'
  self.event('nudge_unseen',row['trigger'],'No activity observed during the response window.',row['nudge_id'],{},now)
  return 'unseen'

 def prune(self,now=None):
  now=self.clock() if now is None else float(now); days=max(1,int(self.config.get('events_retention_days',90)))
  with self.memory._db_lock: cur=self.memory.db.execute('DELETE FROM companion_events WHERE ts<?',(now-days*86400,)); self.memory.db.commit(); return cur.rowcount
 def recent(self,limit=20):
  with self.memory._db_lock: return [dict(r) for r in self.memory.db.execute('SELECT ts,kind,trigger,reason,nudge_id FROM companion_events WHERE kind="nudge_shown" ORDER BY ts DESC LIMIT ?',(max(0,min(20,int(limit))),)).fetchall()]
