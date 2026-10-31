import os,sys,unittest
sys.path.insert(0,os.path.dirname(__file__))
from intent_router import IntentRouter
from companion.consent_dialog import parse_reply, render_batch, should_deliver_consent

from _studybuddy_test_support import PROJECT_ROOT
ROOT=str(PROJECT_ROOT)
class Clock:
    def __init__(self): self.value=1000.0
    def __call__(self): return self.value
    def advance(self,n): self.value+=n

class ConsentDialogTests(unittest.TestCase):
    def setUp(self): self.clock=Clock(); self.router=IntentRouter(os.path.join(ROOT,'config','intent_dictionary.json'),clock=self.clock); self.items=[{'id':'a','label':'The user is preparing for a networking exam.'},{'id':'b','label':'The user prefers short answers.'},{'id':'c','label':'The user enjoys astronomy.'}]
    def test_batch_prompt_is_clear_and_bounded_to_three(self):
        text=render_batch(self.items+ [{'id':'d','label':'extra'}]); self.assertIn('1.',text); self.assertIn('3.',text); self.assertNotIn('4.',text)
    def test_yes_no_skip_selection_parsing(self):
        self.assertEqual(parse_reply('yes',3),('confirm',[1,2,3]))
        self.assertEqual(parse_reply('yes 1,3',3),('confirm',[1,3]))
        self.assertEqual(parse_reply('only 2',3),('confirm',[2]))
        self.assertEqual(parse_reply('1 and 3',3),('confirm',[1,3]))
        self.assertEqual(parse_reply('no',3)[0],'reject')
        self.assertEqual(parse_reply('skip',3)[0],'defer')
        self.assertEqual(parse_reply('later',3)[0],'defer')
        self.assertEqual(parse_reply('cancel',3)[0],'cancel')
    def test_confirm_action_is_deterministic_and_carries_full_batch(self):
        self.router.start_consent_fact(self.items)
        result=self.router.advance('yes 1,3')
        self.assertEqual(result['kind'],'confirm_candidates')
        self.assertEqual([x['id'] for x in result['items']],['a','c'])
        self.assertEqual(len(result['all_items']),3)
    def test_ambiguous_once_then_release_and_questions_release(self):
        self.router.start_consent_fact(self.items)
        self.assertEqual(self.router.advance('hmm')['kind'],'reply')
        result=self.router.advance('hmm'); self.assertEqual(result['kind'],'delegate_current')
        self.router.start_consent_fact(self.items)
        result=self.router.advance('Why are you asking me this?')
        self.assertEqual(result['kind'],'delegate_current')
    def test_delivery_gate_enforces_idle_session_cooldown_daily_cap_and_privacy_switches(self):
        base=dict(now=10000,session_started_at=9000,last_interaction_ts=9800,last_prompt_ts=7000,
                  prompt_count_today=1,pause_until=0,idle_seconds=120,cooldown_seconds=1800,daily_cap=3,
                  facts_enabled=True,proactive_enabled=True,busy=False,worker_busy=False,dialog_pending=False,quiet=False)
        self.assertTrue(should_deliver_consent(**base))
        for key,value in (("session_started_at",9950),("last_interaction_ts",9950),("last_prompt_ts",9000),
                          ("prompt_count_today",3),("pause_until",11000),("facts_enabled",False),
                          ("proactive_enabled",False),("busy",True),("worker_busy",True),
                          ("dialog_pending",True),("quiet",True)):
            case=dict(base); case[key]=value
            with self.subTest(key=key): self.assertFalse(should_deliver_consent(**case))

    def test_consent_dialog_expires_and_has_pending_is_expiry_aware(self):
        self.router.start_consent_fact(self.items); self.clock.advance(299); self.assertTrue(self.router.has_pending)
        self.clock.advance(2); self.assertFalse(self.router.has_pending)
        self.assertIsNone(self.router.state)

if __name__=='__main__': unittest.main()
