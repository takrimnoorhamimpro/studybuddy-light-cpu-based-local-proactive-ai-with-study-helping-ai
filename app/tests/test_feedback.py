import os,sys,unittest
from datetime import datetime
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
from memory import Memory
from personal_companion import PersonalCompanion
from companion.feedback import FeedbackLogger
class Clock:
 def __init__(self,v): self.v=v
 def __call__(self): return self.v
 def advance(self,s): self.v+=s
class FeedbackTests(unittest.TestCase):
 def setUp(self): self.clock=Clock(datetime(2026,10,28,12).timestamp()); self.mem=Memory(':memory:'); self.pc=PersonalCompanion(self.mem); self.fb=FeedbackLogger(self.mem,{'events_retention_days':90},clock=self.clock)
 def tearDown(self): self.mem.close()
 def test_beta_updates_and_floor(self):
  self.assertEqual(self.fb.acceptance('test',12),.5)
  self.fb.update('test',12,success=True,weight=1); self.assertGreater(self.fb.acceptance('test',12),.5)
  for _ in range(10): self.fb.update('test',12,success=False,weight=2)
  self.assertGreaterEqual(self.fb.acceptance('test',12),.15)
 def test_unseen_is_not_negative_and_active_ignore_is_soft_failure(self):
  self.fb.event('nudge_shown','study','reason','n1',now=self.clock())
  self.clock.advance(16*60); self.assertEqual(self.fb.evaluate_pending(active=False),'unseen')
  row=self.mem.db.execute("SELECT beta FROM trigger_stats WHERE trigger='study'").fetchone(); self.assertIsNone(row)
  self.fb.event('nudge_shown','study','reason','n2',now=self.clock()); self.clock.advance(16*60)
  self.assertEqual(self.fb.evaluate_pending(active=True),'ignored')
  row=self.mem.db.execute("SELECT beta FROM trigger_stats WHERE trigger='study'").fetchone(); self.assertAlmostEqual(row['beta'],1.3)
 def test_two_active_ignores_halve_trigger_rate_for_seven_days(self):
  self.fb.event('nudge_shown','study','reason','n1',now=self.clock())
  self.clock.advance(16*60); self.fb.evaluate_pending(active=True)
  self.clock.advance(60); self.fb.event('nudge_shown','study','reason','n2',now=self.clock())
  self.clock.advance(16*60); self.fb.evaluate_pending(active=True)
  until=float(self.pc.setting('trigger_halved_until_study','0'))
  self.assertGreater(until,self.clock()); self.assertAlmostEqual(until-self.clock(),7*86400)
 def test_retention_prunes_old_events(self):
  self.fb.event('nudge_shown','study','old','old',now=self.clock()-100*86400)
  self.fb.event('nudge_shown','study','new','new',now=self.clock())
  self.assertEqual(self.fb.prune(),1); self.assertEqual(len(self.fb.recent()),1)
 def test_event_does_not_store_message_body_or_title(self):
  self.fb.event('nudge_shown','title_clue','title mentions a topic','id',{'app_category':'Browser'})
  row=self.mem.db.execute('SELECT * FROM companion_events').fetchone(); self.assertNotIn('message',row.keys()); self.assertNotIn('window_title',row.keys())
if __name__=='__main__': unittest.main(verbosity=2)
