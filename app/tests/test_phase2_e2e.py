import os,sys,unittest
from datetime import datetime
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
from _studybuddy_test_support import PROJECT_ROOT
ROOT=str(PROJECT_ROOT)
from memory import Memory
from personal_companion import PersonalCompanion
from companion.open_loops import OpenLoopTracker
from companion.feedback import FeedbackLogger
from companion.initiative import InitiativePolicy
from intent_router import IntentRouter
from companion.privacy import PrivacyService
from chat_core import ChatCore
class Clock:
 def __init__(self,v): self.v=v
 def __call__(self): return self.v
 def advance(self,s): self.v+=s
class Phase2E2ETests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock(datetime(2026,11,17,12).timestamp()); self.mem=Memory(':memory:'); self.pc=PersonalCompanion(self.mem)
  self.config={'loop_escalation_days':[3,1,0],'initiative_quiet_default':'23:00-07:00','trigger_threshold':.08,'nudge_cooldown_minutes':60,'daily_cap_default':3}
  due=datetime(2026,11,20).timestamp()+86400
  self.node=self.pc.add_fact("The user's networking exam is on Nov 20.",kind='deadline',evidence='My networking exam is on Nov 20.',confirmed=True,consented=True,expires_ts=due)
  self.loops=OpenLoopTracker(self.mem,self.pc,self.config,clock=self.clock)
  self.fb=FeedbackLogger(self.mem,self.config,clock=self.clock)
  self.policy=InitiativePolicy(self.mem,self.pc,self.config,open_loops=self.loops,feedback=self.fb,clock=self.clock)
  self.router=IntentRouter(os.path.join(ROOT,'config','intent_dictionary.json'),clock=self.clock)
  self.state={'proactive_enabled':True,'last_interaction_ts':self.clock()-600,'last_nudge_ts':0,'limiter_last_sent_ts':0,'limiter_daily_count':0,'daily_cap':3}
  self.ctx={'app_category':'Study/Docs','quiet':False,'quiet_explicit':False}
 def tearDown(self): self.mem.close()
 def test_confirmed_deadline_nudge_later_why_and_tier_off(self):
  d=self.policy.evaluate(now=self.clock(),ctx=self.ctx,state=self.state)
  self.assertEqual(d.trigger,'open_loop_due'); self.assertIn('networking exam',d.reason); self.assertIn('3 days',d.text)
  self.policy.record_shown(d,self.clock()); self.router.start_nudge_checkin(nudge_id=d.nudge_id,trigger=d.trigger,loop_id=d.meta['loop_id'],suggested_message='Help me plan revision for my networking exam.')
  action=self.router.advance('later'); self.assertEqual(action['kind'],'nudge_later'); self.loops.snooze_next(d.meta['loop_id'],now=self.clock())
  loop=self.mem.db.execute("SELECT status,snoozed_until FROM open_loops WHERE id=?",(d.meta['loop_id'],)).fetchone()
  self.assertEqual(loop['status'],'snoozed'); self.assertIsNotNone(loop['snoozed_until']); self.assertGreater(loop['snoozed_until'],self.clock())
  self.assertIn('last nudge was a open_loop_due',self.policy.explain_last())
  self.pc.set_setting('tier_loops','0'); self.assertIsNone(self.policy.evaluate(now=self.clock()+3601,ctx=self.ctx,state={**self.state,'last_nudge_ts':0,'limiter_last_sent_ts':0}))
 def test_nudge_dialog_later_answer_does_not_call_llm(self):
  class FakeLLM:
   def __init__(self): self.calls=0
   def chat(self,messages): self.calls+=1; return "fake response"
  fake=FakeLLM(); core=ChatCore(ROOT,llm=fake,memory=self.mem)
  result=core._apply_intent_dialog('later',{'kind':'nudge_later','loop_id':'missing-loop'})
  self.assertEqual(result['flag'],'nudge_checkin'); self.assertEqual(fake.calls,0)
 def test_tier_off_collects_no_rhythm_and_privacy_deletes_all_proactive(self):
  self.pc.set_setting('tier_rhythm','0'); self.assertFalse(self.pc.setting('tier_rhythm')=='1')
  self.policy.record_shown(type('D',(),{'trigger':'session_return','reason':'neutral','nudge_id':'n2','meta':{}})(),self.clock())
  counts=PrivacyService(self.mem).delete_all_proactive_data(); self.assertIn('rhythm_buckets',counts)
  self.assertEqual(self.mem.db.execute('SELECT COUNT(*) FROM companion_events').fetchone()[0],0)
if __name__=='__main__': unittest.main(verbosity=2)
