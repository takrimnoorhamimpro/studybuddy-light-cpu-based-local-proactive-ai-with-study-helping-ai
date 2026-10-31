"""End-to-end Phase 1 tests with local SQLite and a prompt-capturing fake model."""
import os,sys,tempfile,unittest,time
sys.path.insert(0,os.path.dirname(__file__))
from chat_core import ChatCore
from memory import Memory
from personal_companion import PersonalCompanion
from companion.privacy import PrivacyService
from companion.migrations import migrate

from _studybuddy_test_support import PROJECT_ROOT
ROOT=str(PROJECT_ROOT)
class FakeLLM:
    def __init__(self): self.calls=0; self.messages=[]; self.options=[]
    def chat(self,messages): self.calls+=1; self.messages.append(messages); return 'I can help you plan that.'

class Phase1E2ETests(unittest.TestCase):
    def test_candidate_never_injected_before_yes_then_retrieved_in_next_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'memory.db'); mem=Memory(path); llm=FakeLLM(); core=ChatCore(ROOT,llm=llm,memory=mem)
            result=core.handle_message("I'm preparing for my networking exam on Nov 20.")
            self.assertEqual(result['reply'],'I can help you plan that.')
            queued=core.personal_companion.queued_candidates(limit=3)
            self.assertEqual(len(queued),1); self.assertEqual(queued[0]['status'],'candidate')
            self.assertNotIn('networking exam',llm.messages[-1][0]['content'])
            prompt=core.intent_router.start_consent_fact(queued)
            core.history.append({'role':'assistant','content':prompt}); mem.add_turn('assistant',prompt)
            before=llm.calls; consent=core.handle_message('yes')
            self.assertEqual(consent['flag'],'companion_consent'); self.assertEqual(llm.calls,before)
            node=core.personal_companion.list_nodes()[0]
            self.assertEqual(node['status'],'confirmed'); self.assertEqual(node['consented'],1)
            self.assertTrue(mem.db.execute("SELECT 1 FROM consent_ledger WHERE scope='facts' AND granted=1").fetchone())
            mem.close()
            mem2=Memory(path); llm2=FakeLLM(); core2=ChatCore(ROOT,llm=llm2,memory=mem2)
            core2._ask_model('How should I plan my week?',use_tools=False)
            prompt2=llm2.messages[-1][0]['content']
            self.assertIn('networking exam',prompt2)
            self.assertIn('The user',prompt2)
            core2._ask_model('hello',use_tools=False)
            self.assertNotIn('Things the user chose to tell you',llm2.messages[-1][0]['content'])
            self.assertGreaterEqual(mem2.db.execute("SELECT use_count FROM personal_nodes WHERE status='confirmed'").fetchone()[0],1)
            mem2.close()
    def test_tier_off_disables_extraction_prompting_and_injection(self):
        mem=Memory(':memory:'); core=ChatCore(ROOT,llm=FakeLLM(),memory=mem)
        core.personal_companion.set_setting('tier_facts','0')
        core.handle_message("I'm preparing for my networking exam on Nov 20.")
        self.assertEqual(core.personal_companion.queued_candidates(),[])
        core.personal_companion.add_fact('The user is studying networking',kind='area',confirmed=True,consented=True)
        self.assertEqual(core.user_model.persona_context('networking exam').text,'')
        mem.close()
    def test_turning_facts_tier_off_prevents_pending_consent_confirmation(self):
        mem=Memory(':memory:'); core=ChatCore(ROOT,llm=FakeLLM(),memory=mem); pc=core.personal_companion
        node=pc.add_fact('The user is studying physics','area',source='conversation')
        pc.queue_candidate(node); pc.set_facts_tier(False)
        core.intent_router.start_consent_fact(pc.queued_candidates(limit=3))
        result=core.handle_message('yes')
        self.assertIn('turned off',result['reply'])
        self.assertEqual(pc.list_nodes()[0]['status'],'candidate')
        self.assertEqual(pc.list_nodes()[0]['consented'],0)
        mem.close()

    def test_name_bridge_uses_existing_profile_only_after_confirmation(self):
        mem=Memory(':memory:'); llm=FakeLLM(); core=ChatCore(ROOT,llm=llm,memory=mem)
        core.intent_router.start_onboarding()
        self.assertIn('What name',core.handle_message('yes')['reply'])
        self.assertIn('remember',core.handle_message('Takrim')['reply'])
        before=llm.calls
        result=core.handle_message('yes')
        self.assertEqual(result['flag'],'companion_consent')
        self.assertEqual(llm.calls,before)
        self.assertEqual(mem.get_profile('user_name'),'Takrim')
        node=core.personal_companion.list_nodes()[0]
        self.assertEqual(node['status'],'confirmed')
        mem.close()
    def test_transparency_answer_is_deterministic_and_lists_confirmed_nodes_only(self):
        mem=Memory(':memory:'); llm=FakeLLM(); core=ChatCore(ROOT,llm=llm,memory=mem)
        core.personal_companion.add_fact('The user is studying networking protocols',kind='goal',confirmed=True,consented=True)
        core.personal_companion.add_fact('The user enjoys astronomy',kind='interest',confirmed=False,consented=False)
        result=core.handle_message('What do you remember about me?')
        self.assertIn('networking protocols',result['reply']); self.assertNotIn('astronomy',result['reply'])
        self.assertEqual(llm.calls,0)
        mem.close()

    def test_rejected_candidate_is_not_asked_again_during_cooldown(self):
        mem=Memory(':memory:'); core=ChatCore(ROOT,llm=FakeLLM(),memory=mem)
        core.handle_message("I'm preparing for my networking exam on Nov 20.")
        queued=core.personal_companion.queued_candidates(limit=3)
        core.intent_router.start_consent_fact(queued)
        core.history.append({'role':'assistant','content':'Want me to remember this?'})
        core.handle_message('no')
        self.assertEqual(core.personal_companion.queued_candidates(),[])
        core._extract_candidate_after_turn("I'm preparing for my networking exam on Nov 20.")
        self.assertEqual(core.personal_companion.queued_candidates(),[])
        mem.close()

    def test_two_deferrals_expire_candidate_and_three_rejections_pause_prompts(self):
        mem=Memory(':memory:'); core=ChatCore(ROOT,llm=FakeLLM(),memory=mem); pc=core.personal_companion
        nid=pc.add_fact('The user is studying chemistry','area',source='conversation')
        pc.queue_candidate(nid)
        pc.defer_candidates([nid]); self.assertEqual(pc.list_nodes()[0]['status'],'candidate')
        pc.defer_candidates([nid]); self.assertEqual(pc.list_nodes()[0]['status'],'expired')
        for label in ('The user is studying physics','The user is studying biology','The user is studying history'):
            candidate=pc.add_fact(label,'area',source='conversation'); pc.queue_candidate(candidate)
            core.intent_router.start_consent_fact(pc.queued_candidates(limit=3))
            core.handle_message('no')
        self.assertGreater(float(pc.setting('consent_pause_until','0')),time.time())
        mem.close()

    def test_extra_care_prompt_has_no_persona_context(self):
        mem=Memory(':memory:'); llm=FakeLLM(); core=ChatCore(ROOT,llm=llm,memory=mem)
        core.personal_companion.add_fact('The user is studying networking protocols',kind='goal',confirmed=True,consented=True)
        core._ask_model('How should I plan my study week?',extra_care=True,use_tools=False)
        self.assertNotIn('Things the user chose to tell you',llm.messages[-1][0]['content'])
        mem.close()

    def test_proactive_prompt_never_includes_persona_context(self):
        mem=Memory(':memory:'); llm=FakeLLM(); core=ChatCore(ROOT,llm=llm,memory=mem)
        core.personal_companion.add_fact('The user is studying networking protocols',kind='goal',confirmed=True,consented=True)
        situation='PROACTIVE DESKTOP CONTEXT (limited, local signal only): {"app_name":"Visual Studio Code","app_category":"Coding","window_title":"main.py - Visual Studio Code","private_context":false}'
        core.generate_proactive_message(situation)
        self.assertTrue(llm.messages)
        system=llm.messages[-1][0]['content']
        self.assertNotIn('Things the user chose to tell you',system)
        self.assertNotIn('networking protocols',system)
        mem.close()

    def test_privacy_cascades_candidate_queue(self):
        mem=Memory(':memory:'); pc=PersonalCompanion(mem)
        nid=pc.add_fact('The user is preparing for a physics exam','deadline',evidence='physics exam')
        pc.queue_candidate(nid)
        preview=PrivacyService(mem).preview_forget('physics')
        self.assertEqual(preview['candidate_queue'],1)
        PrivacyService(mem).forget_topic('physics')
        self.assertEqual(pc.queued_candidates(),[])
        nid=pc.add_fact('The user is studying chemistry','area')
        pc.queue_candidate(nid)
        pc.forget_fact(nid)
        self.assertEqual(pc.queued_candidates(),[])
        nid=pc.add_fact('The user likes astronomy','interest'); pc.queue_candidate(nid)
        PrivacyService(mem).wipe_companion_data()
        self.assertEqual(pc.queued_candidates(),[])
        mem.close()
    def test_v1_migration_is_lossless_and_does_not_repeat_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'memory.db'); mem=Memory(path)
            mem.db.execute("CREATE TABLE personal_nodes(id TEXT PRIMARY KEY,label TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'candidate',evidence TEXT NOT NULL DEFAULT '',consented INTEGER NOT NULL DEFAULT 0,created_ts REAL NOT NULL,updated_ts REAL NOT NULL)")
            mem.db.execute("INSERT INTO personal_nodes VALUES('v1-node','Keep v1 node','goal','candidate','v1 evidence',0,1,1)")
            mem.db.execute('PRAGMA user_version=1'); mem.db.commit()
            backup=migrate(mem)
            self.assertTrue(backup and os.path.exists(backup))
            self.assertEqual(mem.db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertEqual(mem.db.execute("SELECT label FROM personal_nodes WHERE id='v1-node'").fetchone()[0],'Keep v1 node')
            self.assertIsNotNone(mem.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_queue'").fetchone())
            backups=[x for x in os.listdir(folder) if '.bak-' in x]
            self.assertEqual(len(backups),1)
            self.assertIsNone(migrate(mem))
            self.assertEqual(len([x for x in os.listdir(folder) if '.bak-' in x]),1)
            mem.close()

if __name__=='__main__': unittest.main()
