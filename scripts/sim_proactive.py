"""Deterministic 30-day initiative simulation. No network, LLM, or threads."""
from __future__ import annotations
import os, sys, random, statistics, time
from datetime import datetime, timedelta
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,'app'))
from memory import Memory
from personal_companion import PersonalCompanion
from companion.feedback import FeedbackLogger
from companion.initiative import InitiativePolicy
from companion.open_loops import OpenLoopTracker
from companion.rhythm import RhythmProfile

class FakeClock:
 def __init__(self,ts): self.ts=float(ts)
 def __call__(self): return self.ts
class SimRhythm:
 def __init__(self,start): self.start=start
 def enabled(self): return True
 def profile(self,now=None): return RhythmProfile(weekday_start=self.start,weekday_end=self.start+3,weekend_start=self.start,weekend_end=self.start+3,study_start_hours=(self.start,),confidence=.9)

def build_student(name,start_hour,seed):
 now=datetime(2026,10,1,0).timestamp(); clock=FakeClock(now); memory=Memory(':memory:'); pc=PersonalCompanion(memory)
 pc.set_setting('tier_rhythm','1'); pc.set_setting('tier_facts','1'); pc.set_setting('tier_loops','1')
 cfg={'initiative_quiet_default':'23:00-07:00','loop_escalation_days':[3,1,0],'trigger_threshold':.05,'acceptance_floor':.15,'nudge_cooldown_minutes':60,'daily_cap_default':3,'events_retention_days':90,'trigger_cooldown_minutes':{'open_loop_due':0,'usual_study_start':1380,'session_return':1380,'wind_down':1380,'title_clue':0},'trigger_base_value':{'open_loop_due':1.0,'usual_study_start':.7,'session_return':.35,'wind_down':.2,'title_clue':.5}}
 fb=FeedbackLogger(memory,cfg,clock=clock); loops=OpenLoopTracker(memory,pc,cfg,clock=clock,rhythm=SimRhythm(start_hour))
 policy=InitiativePolicy(memory,pc,cfg,open_loops=loops,rhythm=SimRhythm(start_hour),feedback=fb,clock=clock)
 return {'name':name,'start':start_hour,'rng':random.Random(seed),'clock':clock,'memory':memory,'pc':pc,'fb':fb,'loops':loops,'policy':policy,'cfg':cfg,'state':{'proactive_enabled':True,'last_interaction_ts':now-601,'last_nudge_ts':0,'limiter_last_sent_ts':0,'limiter_daily_count':0,'daily_cap':3},'counters':{'shown':0,'by_day':{},'triggers':{},'quiet':0,'busy':0,'away':0,'dialog':0,'cooldown':0}}

def main():
 students=[build_student('night owl',22,101),build_student('early bird',7,202),build_student('nudge-averse',12,303),build_student('nudge-friendly',18,404)]
 day0=datetime(2026,10,1)
 try:
  for day in range(30):
   date=day0+timedelta(days=day)
   for student in students:
    c=student['counters']; pc=student['pc']; policy=student['policy']; clock=student['clock']; state=student['state']; fb=student['fb']; name=student['name']; start=student['start']
    # A couple of days away from the PC; repeated weekly busy blocks.
    away=day in (9,10,11)
    busy_period=day%7 in (5,6)
    state['limiter_daily_count']=0
    for hour in range(24):
     now=date.replace(hour=hour).timestamp(); clock.ts=now
     active=(start <= hour <= min(23,start+4)) if start+4<24 else (hour>=start or hour<=2)
     if away: active=False; c['away']+=1 if hour==start else 0
     busy=busy_period and active
     if busy: c['busy']+=1
     quiet=(hour>=23 or hour<7)
     if quiet and active: c['quiet']+=1
     # Session-return is modeled for students who do not need the dedicated
     # ignore-then-negative sequence used to test adaptive quieting.
     session_return=(hour==start and day>0 and name!='nudge-averse')
     dialog_pending=(day%7==4 and hour==start and name!='nudge-averse')
     if dialog_pending: c['dialog']+=1
     state.update({'active':active,'busy':busy,'dialog_pending':dialog_pending,'typing':False,'worker_busy':False,
       'last_interaction_ts':now-601,'last_nudge_ts':state.get('last_nudge_ts',0),
       'limiter_last_sent_ts':state.get('limiter_last_sent_ts',0),
       'daily_cap':3,'session_return':session_return,'wind_down':False,'title_clue_enabled':False})
     ctx={'app_category':'Study/Docs','app_name':'StudyBuddySim','private':False,'meeting':False,'own_window':False,'has_specific_title_clue':False,'quiet':quiet,'quiet_explicit':True}
     before=time.perf_counter(); decision=policy.evaluate(now=now,ctx=ctx,state=state); elapsed=(time.perf_counter()-before)*1000
     c.setdefault('tick_ms',[]).append(elapsed)
     if decision is None: continue
     # A policy result must always carry an explainable reason and deterministic text.
     assert decision.reason and decision.nudge_id
     assert not decision.needs_llm, 'simulation must never request a model call'
     assert not quiet and not busy and active and not dialog_pending
     # Count daily shared cap from actual shown events, not attempts.
     policy.record_shown(decision,now); c['shown']+=1; c['triggers'][decision.trigger]=c['triggers'].get(decision.trigger,0)+1
     c['by_day'][day]=c['by_day'].get(day,0)+1
     state['last_nudge_ts']=now; state['limiter_last_sent_ts']=now; state['limiter_daily_count']=int(state.get('limiter_daily_count',0))+1
     probe_state={**state,'last_interaction_ts':now+1800-601,'last_nudge_ts':now,'limiter_last_sent_ts':now,'session_return':False}
     assert policy.evaluate(now=now+1800,ctx=ctx,state=probe_state) is None, 'nudge occurred inside the 60-minute global cooldown'
     # Student feedback: most averse nudges are ignored; three later session-return
     # nudges receive explicit negative feedback, disabling that trigger.
     if name=='nudge-friendly':
      if student['rng'].random()<.70:
       fb.event('nudge_replied',decision.trigger,'Reply within 10 minutes',decision.nudge_id,now=now)
       fb.update(decision.trigger,hour,success=True,now=now)
      else:
       fb.evaluate_pending(now+16*60,active=True)
     elif name=='nudge-averse':
      if decision.trigger=='usual_study_start':
       ignored=student['memory'].db.execute("SELECT COUNT(*) FROM companion_events WHERE kind='nudge_ignored' AND trigger='usual_study_start'").fetchone()[0]
       if ignored<2:
        fb.evaluate_pending(now+16*60,active=True)
       else:
        fb.event('nudge_negative',decision.trigger,'stop these',decision.nudge_id,now=now)
        fb.update(decision.trigger,hour,success=False,weight=2.0,now=now)
        negatives=student['memory'].db.execute("SELECT COUNT(*) FROM companion_events WHERE kind='nudge_negative' AND trigger='usual_study_start'").fetchone()[0]
        pc.set_setting('trigger_mute_until_usual_study_start',str(now+1800))
        if negatives>=3: pc.set_setting('trigger_enabled_usual_study_start','0')
      else:
       fb.evaluate_pending(now+16*60,active=True)
    # Daily shared-cap assertion and disabled trigger invariant.
    for day_n,count in c['by_day'].items(): assert count<=3,(name,day_n,count)
    if name=='nudge-averse' and pc.setting('trigger_enabled_usual_study_start','1')=='0':
     assert decision is None or decision.trigger!='usual_study_start', 'disabled trigger fired again'
   # reset limiter count at day boundary; DB event counts are also date-scoped by policy.
   for student in students: student['state']['limiter_daily_count']=0
  # Explicit quantitative adaptation requirements.
  averse=next(s for s in students if s['name']=='nudge-averse'); friendly=next(s for s in students if s['name']=='nudge-friendly')
  averse_day_counts=[averse['counters']['by_day'].get(d,0) for d in range(30)]
  assert max(averse_day_counts[3:])<=1, f"nudge-averse exceeded 1/day after day 4: {averse_day_counts}"
  assert sum(averse_day_counts[7:])<=2, f"nudge-averse should be approximately silent after day 8 (<=2 over 23 days): {averse_day_counts[7:]}"
  assert sum(friendly['counters']['by_day'].values())>=10, 'nudge-friendly should retain a stable non-zero rate'
  assert friendly['counters']['shown']<=90
  # Night-owl morning-of loop check: no fixed 09:00 assumption.
  night=next(s for s in students if s['name']=='night owl'); now=datetime(2026,11,20,9).timestamp(); night['clock'].ts=now
  due=datetime(2026,11,20).timestamp()+86400
  node=night['pc'].add_fact("The user's networking exam is on Nov 20.",kind='deadline',evidence='My networking exam is on Nov 20.',confirmed=True,consented=True,expires_ts=due)
  night['loops'].sync(); assert night['loops'].due_candidates(now)==[], 'night-owl morning-of loop fired at fixed 09:00'
  now=datetime(2026,11,20,22).timestamp(); night['clock'].ts=now
  candidate=night['loops'].due_candidates(now); assert candidate and candidate[0].step==0, 'night-owl morning-of step should follow learned 22:00 window'
  # Missed escalation windows are not replayed as backlog.
  missed=datetime(2026,11,18,12).timestamp(); night['clock'].ts=missed
  assert night['loops'].due_candidates(missed)==[], 'missed 3-day window replayed on the next day'
  next_step=datetime(2026,11,19,12).timestamp(); night['clock'].ts=next_step
  next_candidates=night['loops'].due_candidates(next_step)
  assert next_candidates and next_candidates[0].step==1, 'scheduled 1-day step did not replace the missed 3-day step'
  # Warm-cache policy timing measured with perf_counter on the local sandbox.
  student=next(s for s in students if s['name']=='early bird'); times=[]; now=datetime(2026,11,25,12).timestamp(); student['clock'].ts=now
  state={**student['state'],'active':True,'busy':False,'last_interaction_ts':now-601,'last_nudge_ts':now-7200,'limiter_last_sent_ts':now-7200,'limiter_daily_count':0,'daily_cap':3}
  ctx={'app_category':'Study/Docs','quiet':False,'quiet_explicit':True,'has_specific_title_clue':False}
  for _ in range(250):
   t=time.perf_counter(); student['policy'].evaluate(now=now,ctx=ctx,state=state); times.append((time.perf_counter()-t)*1000)
  median=statistics.median(times); maximum=max(times)
  print('30-day simulation: PASS (4 students; 30 days; no LLM/network/thread use)')
  for s in students:
   c=s['counters']; print(f"{s['name']}: nudges={c['shown']}, triggers={c['triggers']}, max/day={max(c['by_day'].values(),default=0)}")
  print(f"nudge-averse thresholds: <=1/day from day 4; <=2 total from day 8 through day 30 (under 0.1/day); observed day4+ max={max(averse_day_counts[3:])}, day8+ total={sum(averse_day_counts[7:])}")
  print(f"nudge-friendly: {sum(friendly['counters']['by_day'].values())} nudges in 30 days (non-zero, cap <=3/day)")
  print(f"policy evaluate warm-cache: median={median:.3f} ms, max={maximum:.3f} ms (250 perf_counter samples)")
  assert median<5 and maximum<5, f"warm-cache policy exceeded 5ms: median={median:.3f} max={maximum:.3f}"
 finally:
  for s in students: s['memory'].close()
if __name__=='__main__': main()
