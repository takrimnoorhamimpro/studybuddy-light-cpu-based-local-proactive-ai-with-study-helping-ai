"""Opt-in, coarse time/category rhythm learning. No titles or app names are stored."""
from dataclasses import dataclass
import datetime, math, time
@dataclass
class RhythmProfile:
    weekday_start:int|None=None; weekday_end:int|None=None; weekend_start:int|None=None; weekend_end:int|None=None
    study_start_hours:tuple=(); focus_block_minutes:int|None=None; after_midnight_frequency:float|None=None; confidence:float=0.0
class RhythmTracker:
    def __init__(self,memory,personal_companion,config=None,clock=time.time): self.memory=memory; self.pc=personal_companion; self.config=config or {}; self.clock=clock; self._last_sample=0.0
    def enabled(self): return str(self.pc.setting('tier_rhythm','0')).lower() not in ('0','false','off','no')
    def sample(self, now=None, active=True, app_category='Other'):
        now=self.clock() if now is None else float(now)
        if not self.enabled() or not active or now-self._last_sample<60: return False
        dt=datetime.datetime.fromtimestamp(now); category=str(app_category or 'Other')[:24]
        with self.memory._db_lock:
            self.memory.db.execute("INSERT OR REPLACE INTO rhythm_days(day,updated_ts) VALUES(?,?)",(dt.date().isoformat(),now))
            self.memory.db.execute("DELETE FROM rhythm_days WHERE updated_ts<?",(now-90*86400,))
            self.memory.db.execute("INSERT INTO rhythm_buckets(dow,hour,app_category,minutes,samples,updated_ts) VALUES(?,?,?,?,?,?) ON CONFLICT(dow,hour,app_category) DO UPDATE SET minutes=minutes+1,samples=samples+1,updated_ts=excluded.updated_ts",(dt.weekday(),dt.hour,category,1.0,1,now)); self.memory.db.commit()
        self._last_sample=now; return True
    def delete(self):
        with self.memory._db_lock:
            cur=self.memory.db.execute('DELETE FROM rhythm_buckets'); self.memory.db.execute('DELETE FROM rhythm_days'); self.memory.db.commit(); return cur.rowcount
    def profile(self, now=None):
        if not self.enabled(): return RhythmProfile()
        now=self.clock() if now is None else float(now); cutoff=now-90*86400
        with self.memory._db_lock:
            rows=self.memory.db.execute('SELECT dow,hour,app_category,minutes,samples,updated_ts FROM rhythm_buckets').fetchall()
            days={str(r[0]) for r in self.memory.db.execute('SELECT day FROM rhythm_days WHERE updated_ts>=?',(now-90*86400,)).fetchall()}
        weighted=[]; total=0.0; hour_activity={}; category_hours={}; after=0.0
        half=max(1.0,float(self.config.get('rhythm_half_life_days',14)))*86400
        for r in rows:
            age=max(0,now-float(r['updated_ts'])); decay=0.5**(age/half); amount=float(r['minutes'])*decay
            if amount<=0.05: continue
            dow=int(r['dow']); hour=int(r['hour'])
            total+=amount; hour_activity.setdefault((dow>=5,hour),0); hour_activity[(dow>=5,hour)]+=amount
            if str(r['app_category']).lower() in ('coding','study/docs','browser research','browser'): category_hours[hour]=category_hours.get(hour,0)+amount
            if hour<5: after+=amount
        min_days=int(self.config.get('rhythm_min_days',5))
        if len(days)<min_days or total<float(self.config.get('rhythm_min_minutes',30)): return RhythmProfile(confidence=0.0)
        def window(weekend):
            hrs={h for (w,h),v in hour_activity.items() if w==weekend and v>=max(1.0,total*0.02)}
            if not hrs: return (None,None,0)
            if len(hrs)==24: return (0,23,24)
            starts=[h for h in hrs if (h-1)%24 not in hrs]
            best=(None,None,0)
            for start in starts:
                run=1
                while run<24 and (start+run)%24 in hrs: run+=1
                if run>best[2]: best=(start,(start+run-1)%24,run)
            return best
        ws,we,weekday_run=window(False); es,ee,weekend_run=window(True)
        top=tuple(h for h,_ in sorted(category_hours.items(),key=lambda x:(-x[1],x[0]))[:3])
        longest=max(weekday_run,weekend_run)
        return RhythmProfile(ws,we,es,ee,top,longest*60 if longest else None,after/max(total,1),min(1.0,len(days)/14)*min(1.0,total/120))
    def describe(self, now=None):
        p=self.profile(now)
        if p.confidence<=0: return 'Patterns are still empty. They need at least five distinct active days and enough title-free activity samples.'
        def fmt(h): return 'unknown' if h is None else datetime.time(h).strftime('%I %p').lstrip('0')
        lines=[f"Typical weekday active window: {fmt(p.weekday_start)}–{fmt(p.weekday_end)}.",f"Typical weekend active window: {fmt(p.weekend_start)}–{fmt(p.weekend_end)}."]
        if p.study_start_hours: lines.append('Common study-category hours: '+', '.join(fmt(h) for h in p.study_start_hours)+'.')
        lines.append(f"Pattern confidence: {p.confidence:.0%}.")
        return ' '.join(lines)
