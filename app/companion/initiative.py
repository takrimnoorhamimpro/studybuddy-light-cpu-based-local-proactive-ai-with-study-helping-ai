"""Unified deterministic policy for all companion-initiated messages."""
from dataclasses import dataclass, field
import datetime, hashlib, time
from companion.templates import render_template
@dataclass
class NudgeDecision:
 nudge_id:str; trigger:str; reason:str; text:str|None; needs_llm:bool=False; meta:dict=field(default_factory=dict)
class InitiativePolicy:
 def __init__(self,memory,personal_companion,config=None,*,open_loops=None,rhythm=None,feedback=None,clock=time.time):
  self.memory=memory; self.pc=personal_companion; self.config=config or {}; self.open_loops=open_loops; self.rhythm=rhythm; self.feedback=feedback; self.clock=clock
 def _setting(self,k,default='0'): return str(self.pc.setting(k,default)).lower() not in ('0','false','off','no','')
 def _event_count(self,kind,trigger=None,since=0):
  with self.memory._db_lock:
   if trigger is None: r=self.memory.db.execute('SELECT COUNT(*) FROM companion_events WHERE kind=? AND ts>=?',(kind,since)).fetchone()
   else: r=self.memory.db.execute('SELECT COUNT(*) FROM companion_events WHERE kind=? AND trigger=? AND ts>=?',(kind,trigger,since)).fetchone()
  return int(r[0] or 0)
 def _quiet(self,now,ctx):
  if ctx.get('quiet') is not None: return bool(ctx['quiet'])
  value=str(self.config.get('initiative_quiet_default','23:00-07:00'))
  try: start,end=[datetime.datetime.strptime(x,'%H:%M').time() for x in value.split('-',1)]; cur=datetime.datetime.fromtimestamp(now).time(); return cur>=start or cur<end if start>end else start<=cur<end
  except Exception: return False
 def evaluate(self,*,now=None,ctx=None,state=None):
  now=self.clock() if now is None else float(now); ctx=dict(ctx or {}); state=dict(state or {})
  # Non-negotiable gates run before any scoring or loop mutation.
  if not self._setting('proactive_enabled', '1') or not state.get('proactive_enabled',True): return None
  if state.get('busy') or state.get('dialog_pending') or state.get('typing') or state.get('worker_busy') or state.get('active') is False: return None
  if state.get('paused') or now<float(state.get('pause_until',0)): return None
  # A stable, informative foreground title is useful while the user is active;
  # only long inactivity, private context, or an active chat should suppress it.
  has_window_context=bool(ctx.get('has_window_context') or ctx.get('has_specific_title_clue'))
  # The desktop idle and title-stability checks are the interruption boundary
  # for window prompts. Do not add another elapsed-time cooldown after a chat.
  if not has_window_context and now-float(state.get('last_interaction_ts',0))<120: return None
  if has_window_context and not (0 <= float(state.get('idle_seconds', 0)) < 900): return None
  quiet_explicit=bool(ctx.get('quiet_explicit', True))
  if self._quiet(now,ctx): return None
  if ctx.get('private') or ctx.get('private_context') or ctx.get('meeting') or ctx.get('own_window') or ctx.get('app_category') in ('Private','Meeting'): return None
  cap=max(0,min(5,int(state.get('daily_cap',self.config.get('daily_cap_default',3)))))
  day_start=datetime.datetime.fromtimestamp(now).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
  # Window-title prompts are event-based and deduplicated by the observed context
  # in the desktop app. Do not suppress a genuinely new window because of a timer
  # or a shared daily count. Other initiative types retain their daily safety cap.
  if not has_window_context:
   if int(state.get('limiter_daily_count',0))>=cap: return None
   if self._event_count('nudge_shown',since=day_start)>=cap: return None
  triggers=[]
  base_values=self.config.get('trigger_base_value',{}) if isinstance(self.config.get('trigger_base_value',{}),dict) else {}
  if self._setting('tier_loops','1') and self._setting('tier_facts','1') and self.open_loops:
   for item in self.open_loops.due_candidates(now):
    triggers.append(('open_loop_due',float(base_values.get('open_loop_due',1.0)),1.0,item.reason,item.text or render_template('open_loop_due',item.step,title=item.title,loop_id=item.loop_id),{'loop_id':item.loop_id,'step':item.step_index,'days_left':item.step,'due_ts':item.due_ts,'title':item.title}))
  if self._setting('tier_rhythm','0') and self.rhythm and not ctx.get('relationship_deferred'):
   p=self.rhythm.profile(now)
   if p.confidence>0 and p.study_start_hours:
    hour=datetime.datetime.fromtimestamp(now).hour
    if min(abs(hour-h) for h in p.study_start_hours)<=0:
     triggers.append(('usual_study_start',float(base_values.get('usual_study_start',.7)),1.0,'It is within your learned study-category start hour.',render_template('usual_study_start'),{'hour':hour}))
  if state.get('session_return'):
   triggers.append(('session_return',float(base_values.get('session_return',.35)),1.0,'A new session started after at least eight hours away.',render_template('session_return'),{}))
  if state.get('wind_down'):
   triggers.append(('wind_down',float(base_values.get('wind_down',.2)),1.0,'The configured late-night wind-down time has arrived.',render_template('wind_down'),{}))
  if has_window_context and state.get('title_clue_enabled',True):
   full_title=' '.join(str(ctx.get('window_title') or '').split())[:240]
   title=full_title[:90]
   app=' '.join(str(ctx.get('app_name') or 'that app').split())[:40]
   app_is_in_title=bool(title and app.casefold() in title.casefold())
   location=f' open in {app}' if title and not app_is_in_title else ''
   text=(f'Hey, my guess is you may be looking at “{title}”{location}—am I close?') if title else f"Hey, I noticed {app} is open. Are you free for a quick chat?"
   triggers.append(('title_clue',float(base_values.get('title_clue',.7)),1.0,'A stable foreground window is open during an active study session.',text,{'app_name':ctx.get('app_name','unknown'),'app_category':ctx.get('app_category','Other'),'window_title':full_title}))
  if state.get('cli_mode'):
   triggers=[item for item in triggers if item[0] in ('open_loop_due','session_return')]
  if not triggers: return None
  hour=datetime.datetime.fromtimestamp(now).hour
  scored=[]
  for trigger,base,fit,reason,txt,meta in triggers:
   if not quiet_explicit and trigger in ('open_loop_due','usual_study_start','session_return') and self._quiet(now, {'quiet':None}): continue
   if not self._setting('trigger_enabled_'+trigger,'1'): continue
   cooldowns=self.config.get('trigger_cooldown_minutes',{}) if isinstance(self.config.get('trigger_cooldown_minutes',{}),dict) else {}
   trigger_cooldown=max(0,int(cooldowns.get(trigger,0)))
   if trigger_cooldown and self._event_count('nudge_shown',trigger,now-trigger_cooldown*60): continue
   try:
    if now<float(self.pc.setting('trigger_mute_until_'+trigger,'0') or 0): continue
   except (TypeError,ValueError): pass
   negatives=self._event_count('nudge_ignored',trigger,now-7*86400)
   try: halved_until=float(self.pc.setting('trigger_halved_until_'+trigger,'0') or 0)
   except (TypeError,ValueError): halved_until=0.0
   # A missed title-based check-in is often just someone watching, reading, or
   # working. It is not evidence that they want title curiosity reduced.
   if trigger!='title_clue' and halved_until>now:
    base*=.5
    # A stable parity gate implements a deterministic half-rate for seven days.
    parity=int(hashlib.sha256(trigger.encode()).hexdigest()[:2],16)%2
    if (datetime.datetime.fromtimestamp(now).date().toordinal()+parity)%2: continue
   acceptance=(1.0 if trigger=='title_clue' else
               self.feedback.acceptance(trigger,hour,now) if self.feedback else .5)
   today=self._event_count('nudge_shown',since=day_start)
   recent_ignores=self._event_count('nudge_ignored',trigger,now-86400)
   # For context check-ins, a new title is a new event. Reduce future nudges
   # when the user ignores them, not just because they've had other conversations.
   volume_penalty=0 if trigger=='title_clue' else float(self.config.get('annoyance_per_nudge',.08))*today
   annoyance=(0.0 if trigger=='title_clue' else
              volume_penalty+float(self.config.get('annoyance_per_ignore',.12))*recent_ignores)
   score=base*acceptance*fit-annoyance
   scored.append((score,trigger,reason,txt,meta))
  if not scored: return None
  score,trigger,reason,txt,meta=max(scored,key=lambda x:(x[0],x[1]))
  if score<float(self.config.get('trigger_threshold',.08)): return None
  nid=hashlib.sha256(f'{trigger}|{now:.3f}|{meta}'.encode()).hexdigest()[:20]
  # Build title-based openings asynchronously from the sanitized title so the
  # companion can make a grounded, natural observation without blocking chat.
  return NudgeDecision(nid,trigger,reason,txt,trigger=='title_clue',meta)
 def record_shown(self,decision,now=None):
  now=self.clock() if now is None else float(now)
  if self.feedback:
   safe_meta={k:v for k,v in dict(decision.meta or {}).items() if k not in ('title','app_name','window_title','text','message')}
   self.feedback.event('nudge_shown',decision.trigger,decision.reason,decision.nudge_id,safe_meta,now)
  if decision.trigger=='open_loop_due' and self.open_loops: self.open_loops.mark_surfaced(decision.meta['loop_id'],decision.meta['step'],now)
 def explain_last(self):
  with self.memory._db_lock: row=self.memory.db.execute("SELECT trigger,reason,ts FROM companion_events WHERE kind='nudge_shown' ORDER BY ts DESC LIMIT 1").fetchone()
  if not row: return 'I have not sent an initiative nudge in the available history.'
  return f"The last nudge was a {row['trigger']} check-in because {row['reason']} You can turn it off in Settings under initiative triggers."
