import os, sys, time, unittest
sys.path.insert(0, os.path.dirname(__file__))
from memory import Memory
from personal_companion import PersonalCompanion
from companion.user_model import UserModel, estimate_tokens
from companion.extractor import extract

from _studybuddy_test_support import PROJECT_ROOT
ROOT=str(PROJECT_ROOT)

class UserModelTests(unittest.TestCase):
    def setUp(self):
        self.memory=Memory(':memory:'); self.pc=PersonalCompanion(self.memory)
        import json
        with open(os.path.join(ROOT,'config','companion.json'),encoding='utf-8') as f: self.config=json.load(f)
        self.model=UserModel(self.memory,self.pc,self.config)
    def tearDown(self): self.memory.close()
    def add(self,label,kind='goal',confirmed=True,consented=True,expires_ts=None,sensitivity='normal'):
        return self.pc.add_fact(label,kind=kind,evidence='user provided',confirmed=confirmed,consented=consented,expires_ts=expires_ts,sensitivity=sensitivity)
    def test_only_confirmed_consented_normal_unexpired_nodes_are_eligible(self):
        self.add('The user is preparing for a networking exam on Nov 20.')
        self.add('The user is learning chemistry',confirmed=False,consented=False)
        unconsented=self.add('The user wants to learn algebra',confirmed=False,consented=False)
        self.memory.db.execute("UPDATE personal_nodes SET status='confirmed',consented=0 WHERE id=?",(unconsented,)); self.memory.db.commit()
        self.add('The user has an exam tomorrow',expires_ts=time.time()-1)
        self.add('The user has a private medical diagnosis',sensitivity='sensitive')
        self.add('The user is studying computer science',kind='goal',confirmed=True,consented=True)
        result=self.model.persona_context('Help me plan for my networking exam',budget_tokens=200)
        self.assertIn('networking exam',result.text)
        self.assertNotIn('chemistry',result.text); self.assertNotIn('algebra',result.text)
        self.assertNotIn('medical diagnosis',result.text)
    def test_small_talk_has_no_persona_tokens_without_relevant_preferences(self):
        self.add('The user is preparing for a networking exam on Nov 20.')
        result=self.model.persona_context('hello')
        self.assertEqual((result.text,result.node_ids,result.est_tokens),('',[],0))
    def test_always_on_preferences_are_capped_at_two(self):
        for text in ('The user prefers short answers.','The user prefers examples.','The user prefers bullet points.'):
            self.add(text,kind='preference')
        result=self.model.persona_context('hello',max_items=4)
        self.assertEqual(len(result.node_ids),2)
    def test_budget_enforced_and_low_ranked_items_dropped(self):
        for i in range(4): self.add(f'The user is studying networking protocols chapter {i} with detailed exercises')
        result=self.model.persona_context('Help with networking protocols chapter',budget_tokens=45,max_items=4)
        self.assertLessEqual(result.est_tokens,45)
        self.assertTrue(result.truncated)
    def test_rendered_persona_uses_neutral_third_person(self):
        self.add('The user prefers concise explanations.')
        result=self.model.persona_context('Give me concise explanations')
        self.assertTrue(result.text)
        import re
        self.assertIsNone(re.search(r'\b(?:I|me|my|mine|we|our)\b',result.text,re.I))
    def test_mark_used_only_updates_requested_nodes(self):
        one=self.add('The user is studying networking protocols')
        two=self.add('The user is learning botany')
        self.assertEqual(self.model.mark_used([one]),1)
        rows={r['id']:r for r in self.pc.list_nodes()}
        self.assertEqual(rows[one]['use_count'],1); self.assertIsNone(rows[two]['last_used_ts'])
    def test_facts_tier_defaults_on_and_every_toggle_is_audited(self):
        self.assertEqual(self.pc.setting('tier_facts','1'),'1')
        self.pc.set_facts_tier(False)
        self.pc.set_facts_tier(True)
        rows=self.memory.db.execute("SELECT scope,granted,note FROM consent_ledger WHERE note='facts_tier_toggle' ORDER BY id").fetchall()
        self.assertEqual([(r['scope'],r['granted']) for r in rows],[('facts',0),('facts',1)])

    def test_tier_off_returns_empty_and_transparency_empty(self):
        self.add('The user is studying networking protocols')
        self.pc.set_setting('tier_facts','0')
        self.assertEqual(self.model.persona_context('networking exam').text,'')
        self.assertEqual(self.model.describe_known(),[])
    def test_planning_query_retrieves_active_exam_deadline(self):
        self.add('The user is preparing for their networking exam on Nov 20.',kind='deadline')
        result=self.model.persona_context('How should I plan my week?')
        self.assertIn('networking exam',result.text)
    def test_extraction_plus_retrieval_median_under_ten_ms_for_500_nodes(self):
        for i in range(500): self.add(f'The user is interested in topic area {i} for study planning')
        samples=[]
        for _ in range(5):
            started=time.perf_counter()
            extract("I'm preparing for my networking exam on Nov 20.",ttl_days_by_kind=self.config.get('ttl_days_by_kind'))
            self.model.persona_context('How should I plan my study week?',budget_tokens=200,max_items=4)
            samples.append(time.perf_counter()-started)
        median=sorted(samples)[len(samples)//2]
        self.assertLess(median,0.01, f'Combined extraction/retrieval median was {median*1000:.2f} ms')

    def test_retrieval_under_ten_ms_for_500_nodes(self):
        for i in range(500): self.add(f'The user is interested in topic area {i} for study planning')
        started=time.perf_counter(); self.model.persona_context('Help me plan my study week',budget_tokens=200,max_items=4)
        elapsed=time.perf_counter()-started
        self.assertLess(elapsed,0.01, f'Retrieval took {elapsed*1000:.2f} ms')

if __name__=='__main__': unittest.main()
