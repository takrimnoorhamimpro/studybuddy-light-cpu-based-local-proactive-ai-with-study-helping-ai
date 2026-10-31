import os,sys,unittest
from datetime import datetime
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
from memory import Memory
from personal_companion import PersonalCompanion
from companion.rhythm import RhythmTracker
from companion.privacy import PrivacyService
class Clock:
 def __init__(self,v): self.v=v
 def __call__(self): return self.v
 def advance(self,s): self.v+=s
class RhythmTests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock(datetime(2026,10,28,8).timestamp()); self.mem=Memory(':memory:'); self.pc=PersonalCompanion(self.mem); self.rhythm=RhythmTracker(self.mem,self.pc,{'rhythm_min_days':5,'rhythm_min_minutes':3},clock=self.clock)
 def tearDown(self): self.mem.close()
 def test_off_by_default_and_one_sample_per_minute(self):
  self.assertFalse(self.rhythm.enabled()); self.assertFalse(self.rhythm.sample(active=True,app_category='Coding'))
  self.pc.set_setting('tier_rhythm','1'); self.assertTrue(self.rhythm.sample(active=True,app_category='Coding'))
  self.assertFalse(self.rhythm.sample(active=True,app_category='Coding'))
 def test_samples_store_only_coarse_category(self):
  self.pc.set_setting('tier_rhythm','1'); self.rhythm.sample(active=True,app_category='Browser research')
  cols=[r[1] for r in self.mem.db.execute('PRAGMA table_info(rhythm_buckets)')]
  self.assertEqual(set(cols),{'dow','hour','app_category','minutes','samples','updated_ts'})
  row=dict(self.mem.db.execute('SELECT * FROM rhythm_buckets').fetchone())
  self.assertNotIn('title',row); self.assertNotIn('app_name',row); self.assertEqual(row['app_category'],'Browser research')
 def test_confidence_requires_five_distinct_days_and_reset_deletes(self):
  self.pc.set_setting('tier_rhythm','1')
  for day in range(5):
   self.clock.v=datetime(2026,10,1+day,8).timestamp(); self.rhythm._last_sample=0
   self.rhythm.sample(active=True,app_category='Study/Docs')
  self.assertGreater(self.rhythm.profile().confidence,0)
  PrivacyService(self.mem).delete_rhythm(); self.assertEqual(self.rhythm.profile().confidence,0)
 def test_turning_tier_off_stops_sampling(self):
  self.pc.set_setting('tier_rhythm','1'); self.rhythm.sample(active=True); self.pc.set_setting('tier_rhythm','0'); self.clock.advance(61)
  self.assertFalse(self.rhythm.sample(active=True)); self.assertEqual(self.mem.db.execute('SELECT COUNT(*) FROM rhythm_buckets').fetchone()[0],1)
if __name__=='__main__': unittest.main(verbosity=2)
