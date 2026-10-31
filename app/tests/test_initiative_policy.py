import os,sys,unittest
from datetime import datetime
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
from memory import Memory
from personal_companion import PersonalCompanion
from companion.initiative import InitiativePolicy
from companion.feedback import FeedbackLogger
from companion.templates import TEMPLATES,BANNED_PHRASES,render_template
class Clock:
 def __init__(self,v): self.v=v
 def __call__(self): return self.v
class Profile:
 confidence=.9; study_start_hours=(14,); weekday_start=14; weekend_start=14
class Rhythm:
 def enabled(self): return True
 def profile(self,now=None): return Profile()
class InitiativePolicyTests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock(datetime(2026,10,28,14).timestamp()); self.mem=Memory(':memory:'); self.pc=PersonalCompanion(self.mem)
  self.pc.set_setting('tier_rhythm','1'); self.fb=FeedbackLogger(self.mem,{'acceptance_floor':.15},clock=self.clock)
  self.policy=InitiativePolicy(self.mem,self.pc,{'initiative_quiet_default':'23:00-07:00','trigger_threshold':.08,'daily_cap_default':3,'nudge_cooldown_minutes':60},rhythm=Rhythm(),feedback=self.fb,clock=self.clock)
  self.state={'proactive_enabled':True,'last_interaction_ts':self.clock()-600,'last_nudge_ts':0,'limiter_last_sent_ts':0,'limiter_daily_count':0,'daily_cap':3}
  self.ctx={'app_category':'Study/Docs','quiet':False,'quiet_explicit':False}
 def tearDown(self): self.mem.close()
 def test_usual_study_start_is_deterministic_and_explainable(self):
  d=self.policy.evaluate(now=self.clock(),ctx=self.ctx,state=self.state); self.assertEqual(d.trigger,'usual_study_start'); self.assertTrue(d.reason); self.assertEqual(d.text,render_template('usual_study_start'))
 def test_hard_gates_busy_dialog_quiet_and_recent_message(self):
  for state,ctx in [({**self.state,'busy':True},self.ctx),({**self.state,'dialog_pending':True},self.ctx),({**self.state,'last_interaction_ts':self.clock()-30},self.ctx), (self.state,{**self.ctx,'quiet':True})]:
   self.assertIsNone(self.policy.evaluate(now=self.clock(),ctx=ctx,state=state))
 def test_daily_cap_applies_without_a_blanket_elapsed_time_cooldown(self):
  self.assertIsNone(self.policy.evaluate(now=self.clock(),ctx=self.ctx,state={**self.state,'limiter_daily_count':3}))
  self.assertIsNotNone(self.policy.evaluate(now=self.clock(),ctx=self.ctx,state={**self.state,'limiter_last_sent_ts':self.clock()-1800}))
 def test_reenable_setting_restores_trigger_after_disable(self):
  self.pc.set_setting('trigger_enabled_usual_study_start','0'); self.assertIsNone(self.policy.evaluate(now=self.clock(),ctx=self.ctx,state=self.state))
  self.pc.set_setting('trigger_enabled_usual_study_start','1'); self.assertIsNotNone(self.policy.evaluate(now=self.clock(),ctx=self.ctx,state=self.state))
 def test_private_meeting_own_window_pause_typing_and_worker_gates(self):
  for ctx in ({**self.ctx,'private':True},{**self.ctx,'meeting':True},{**self.ctx,'own_window':True}):
   self.assertIsNone(self.policy.evaluate(now=self.clock(),ctx=ctx,state=self.state))
  for state in ({**self.state,'paused':True},{**self.state,'typing':True},{**self.state,'worker_busy':True}):
   self.assertIsNone(self.policy.evaluate(now=self.clock(),ctx=self.ctx,state=state))
  self.pc.set_setting('tier_rhythm','0')
  self.assertIsNone(self.policy.evaluate(now=self.clock(),ctx=self.ctx,state=self.state))
 def test_deferring_personal_questions_does_not_suppress_window_curiosity(self):
  ctx={**self.ctx,'app_name':'chrome','window_title':'The Matrix Reloaded - YouTube',
       'has_window_context':True,'has_specific_title_clue':True,
       'relationship_deferred':True}
  state={**self.state,'idle_seconds':15,'title_clue_enabled':True}
  decision=self.policy.evaluate(now=self.clock(),ctx=ctx,state=state)
  self.assertIsNotNone(decision)
  self.assertEqual(decision.trigger,'title_clue')
 def test_per_trigger_cooldown_prevents_repeat_until_expiry(self):
  self.policy.config['trigger_cooldown_minutes']={'usual_study_start':1380}
  d=self.policy.evaluate(now=self.clock(),ctx=self.ctx,state=self.state); self.assertIsNotNone(d)
  self.policy.record_shown(d,self.clock())
  self.assertIsNone(self.policy.evaluate(now=self.clock()+3600,ctx=self.ctx,state={**self.state,'last_nudge_ts':0,'limiter_last_sent_ts':0}))
  self.assertIsNotNone(self.policy.evaluate(now=self.clock()+86400,ctx=self.ctx,state={**self.state,'last_nudge_ts':0,'limiter_last_sent_ts':0}))
 def test_all_templates_are_free_of_banned_phrases_and_stable(self):
  for trigger,steps in TEMPLATES.items():
   for step,templates in steps.items():
    for template in templates:
     for phrase in BANNED_PHRASES: self.assertNotIn(phrase,template.casefold())
    self.assertEqual(render_template(trigger,step,loop_id='same'),render_template(trigger,step,loop_id='same'))
 def test_at_most_one_decision_and_reason_is_stored(self):
  d=self.policy.evaluate(now=self.clock(),ctx=self.ctx,state={**self.state,'session_return':True,'wind_down':True})
  self.assertIsNotNone(d); self.policy.record_shown(d,self.clock())
  row=self.mem.db.execute("SELECT reason,nudge_id FROM companion_events WHERE kind='nudge_shown'").fetchone()
  self.assertTrue(row['reason']); self.assertEqual(row['nudge_id'],d.nudge_id)
if __name__=='__main__': unittest.main(verbosity=2)
