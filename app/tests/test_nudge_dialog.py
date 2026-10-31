import os,sys,unittest
from datetime import datetime
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
from _studybuddy_test_support import PROJECT_ROOT
from intent_router import IntentRouter
class Clock:
 def __init__(self): self.v=1000.0
 def __call__(self): return self.v
 def advance(self,s): self.v+=s
class NudgeDialogTests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock(); self.router=IntentRouter(os.path.join(str(PROJECT_ROOT),'config','intent_dictionary.json'),clock=self.clock)
 def test_yes_hands_one_prewritten_message(self):
  self.router.start_nudge_checkin(nudge_id='n',trigger='open_loop_due',loop_id='l',suggested_message='Help me plan revision for my exam.')
  result=self.router.advance('yes'); self.assertEqual(result['kind'],'nudge_yes'); self.assertEqual(result['original_text'],'Help me plan revision for my exam.'); self.assertFalse(self.router.has_pending)
 def test_later_and_stop_are_deterministic(self):
  self.router.start_nudge_checkin(nudge_id='n',trigger='open_loop_due',loop_id='l'); self.assertEqual(self.router.advance('later')['kind'],'nudge_later')
  self.router.start_nudge_checkin(nudge_id='n',trigger='open_loop_due',loop_id='l'); self.assertEqual(self.router.advance('stop reminding me')['kind'],'nudge_stop')
 def test_question_releases_to_normal_chat_and_expiry_releases(self):
  self.router.start_nudge_checkin(nudge_id='n',trigger='open_loop_due'); self.assertEqual(self.router.advance('why did you message me?')['kind'],'delegate_current')
  self.router.start_nudge_checkin(nudge_id='n',trigger='open_loop_due'); self.clock.advance(301); self.assertFalse(self.router.has_pending)
 def test_one_reminder_then_release(self):
  self.router.start_nudge_checkin(nudge_id='n',trigger='open_loop_due'); self.assertEqual(self.router.advance('maybe')['kind'],'reply'); self.assertEqual(self.router.advance('still maybe')['kind'],'delegate_current')
 def test_rhythm_consent_is_local_deterministic(self):
  self.router.start_rhythm_consent(); self.assertEqual(self.router.advance('yes please')['kind'],'enable_rhythm')
  self.router.start_rhythm_consent(); self.assertEqual(self.router.advance('no thanks')['kind'],'decline_rhythm')
if __name__=='__main__': unittest.main(verbosity=2)
