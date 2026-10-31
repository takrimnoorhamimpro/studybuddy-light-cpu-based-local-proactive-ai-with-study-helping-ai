import os,sys,unittest
from datetime import datetime
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
from memory import Memory
from personal_companion import PersonalCompanion
from companion.open_loops import OpenLoopTracker
class Clock:
 def __init__(self,v): self.v=v
 def __call__(self): return self.v
 def advance(self,s): self.v+=s
class OpenLoopTests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock(datetime(2026,11,17,12).timestamp()); self.mem=Memory(':memory:'); self.pc=PersonalCompanion(self.mem)
  self.config={'loop_escalation_days':[3,1,0],'loop_morning_default':'09:00'}
  self.tracker=OpenLoopTracker(self.mem,self.pc,self.config,clock=self.clock)
  due=datetime(2026,11,20).timestamp()+86400
  self.node=self.pc.add_fact("The user's networking exam is on Nov 20.",kind='deadline',evidence='My networking exam is on Nov 20.',confirmed=True,consented=True,expires_ts=due)
 def tearDown(self): self.mem.close()
 def test_sync_idempotent_and_one_nudge_per_step(self):
  self.tracker.sync(); self.tracker.sync(); self.assertEqual(len(self.tracker.list_open()),1)
  n=self.tracker.due_candidates(self.clock())[0]; self.assertIn('3 days',n.reason)
  self.tracker.mark_surfaced(n.loop_id,n.step_index,self.clock()); self.assertEqual(self.tracker.due_candidates(self.clock()),[])
  self.clock.advance(2*86400); self.assertEqual(self.tracker.due_candidates(self.clock())[0].step,1)
 def test_forgetting_or_unconfirming_node_removes_loop(self):
  self.tracker.sync(); self.pc.forget_fact(self.node); self.tracker.sync(); self.assertEqual(self.tracker.list_open(),[])
 def test_unconfirmed_node_never_creates_loop(self):
  self.pc.forget_fact(self.node)
  self.pc.add_fact("The user's exam is on Nov 20.",kind='deadline',evidence='My exam is on Nov 20',confirmed=False,consented=False)
  self.tracker.sync(); self.assertEqual(self.tracker.list_open(),[])
 def test_done_and_dropped_loops_do_not_surface(self):
  self.tracker.sync(); loop=self.tracker.list_open()[0]; self.tracker.mark(loop.id,'done'); self.assertEqual(self.tracker.due_candidates(self.clock()),[])
 def test_task_due_source_does_not_duplicate_explicit_reminder_at_due_time(self):
  due=datetime(2026,11,20).timestamp()
  with self.mem._db_lock:
   self.mem.db.execute("INSERT INTO tasks(title,details,due_ts,status,created_ts) VALUES(?,?,?,?,?)",('Networking assignment','',due,'open',self.clock()))
   self.mem.db.execute("INSERT INTO reminders(text,remind_ts,fired,created_ts) VALUES(?,?,0,?)",('Networking assignment due',due,self.clock())); self.mem.db.commit()
  self.clock.v=datetime(2026,11,20,10).timestamp(); self.tracker.sync()
  candidates=self.tracker.due_candidates(self.clock())
  self.assertFalse(any(x.title=='Networking assignment' for x in candidates))
 def test_morning_of_only_in_morning_window(self):
  self.clock.v=datetime(2026,11,20,12).timestamp(); self.tracker.sync(); self.assertEqual(self.tracker.due_candidates(self.clock()),[])
if __name__=='__main__': unittest.main(verbosity=2)
