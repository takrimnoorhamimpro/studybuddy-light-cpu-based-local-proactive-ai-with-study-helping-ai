import os, sys, time, unittest
sys.path.insert(0, os.path.dirname(__file__))
from memory import Memory
from personal_companion import PersonalCompanion

class PersonalCompanionTests(unittest.TestCase):
    def setUp(self):
        self.mem=Memory(':memory:'); self.pc=PersonalCompanion(self.mem)
    def tearDown(self): self.mem.close()
    def test_confirmed_facts_require_consent(self):
        with self.assertRaises(ValueError): self.pc.add_fact('Takrim','profile',confirmed=True,consented=False)
        node=self.pc.add_fact('Takrim','profile',evidence='user said name',confirmed=True,consented=True)
        self.assertEqual(self.pc.list_nodes()[0]['status'],'confirmed')
    def test_candidate_is_not_confirmed(self):
        nid=self.pc.add_fact('physics simulations','interest',evidence='repeated topic')
        self.assertEqual(self.pc.list_nodes()[0]['status'],'candidate')
        self.assertTrue(self.pc.confirm_fact(nid)); self.assertEqual(self.pc.list_nodes()[0]['consented'],1)
    def test_forget_removes_node(self):
        nid=self.pc.add_fact('Takrim','profile')
        self.assertTrue(self.pc.forget_fact(nid)); self.assertEqual(self.pc.list_nodes(),[])
    def test_followup_persists_and_delivers_once(self):
        fid=self.pc.schedule_followup(time.time()-1,payload={'topic':'onboarding'})
        self.assertEqual(len(self.pc.due_followups()),1)
        self.assertEqual(self.pc.due_followups(),[])
    def test_followup_can_cancel(self):
        fid=self.pc.schedule_followup(time.time()+1000)
        self.assertTrue(self.pc.cancel_followup(fid)); self.assertEqual(self.pc.due_followups(time.time()+2000),[])

if __name__=='__main__': unittest.main()
