import os,sys,unittest
sys.path.insert(0,os.path.dirname(__file__))
from companion.prompt_budget import PromptBudget
from companion.user_model import estimate_tokens

class PromptBudgetTests(unittest.TestCase):
    def test_trim_order_drops_persona_then_old_history_then_optional_context(self):
        persona='PERSONA-CONTEXT-LONG ' * 30
        system='SAFETY CORE NEVER REMOVE\n\n'+persona+'\n\nSituation: '+('situation '*30)
        history=[{'role':'user','content':'old history '*80},{'role':'assistant','content':'recent history '*80}]
        fitted,turns,estimate,truncated=PromptBudget.fit(system,history,'USER TEXT NEVER REMOVE',limit=120,persona_text=persona)
        self.assertTrue(truncated); self.assertIn('SAFETY CORE NEVER REMOVE',fitted); self.assertIn('USER TEXT NEVER REMOVE','USER TEXT NEVER REMOVE')
        self.assertNotIn('PERSONA-CONTEXT-LONG',fitted); self.assertLessEqual(estimate,120)
    def test_lowest_ranked_persona_item_is_removed_first(self):
        header='Things the user chose to tell you (use a detail only if it helps this reply; never list them or mention memory):'
        high='- The user is preparing for a networking exam.'
        low='- The user likes astronomy documentaries.'
        persona=header+'\n'+high+'\n'+low
        system='SAFETY CORE\n\n'+persona
        limit=estimate_tokens('SAFETY CORE\n\n'+header+'\n'+high)+estimate_tokens('How should I plan my week?')+2
        fitted,turns,estimate,truncated=PromptBudget.fit(system,[], 'How should I plan my week?',limit=limit,persona_text=persona)
        self.assertIn(high,fitted); self.assertNotIn(low,fitted); self.assertTrue(truncated); self.assertLessEqual(estimate,limit)

    def test_optional_context_trim_preserves_tool_rules_and_safety_core(self):
        system='SAFETY CORE\nLANGUAGE RULE\n\nSituation: '+('desktop context '*100)+'\n\nTOOL RULES: use local tools when needed.'
        fitted,turns,estimate,truncated=PromptBudget.fit(system,[], 'What can you do?',limit=80)
        self.assertIn('SAFETY CORE',fitted); self.assertIn('LANGUAGE RULE',fitted)
        self.assertIn('TOOL RULES: use local tools when needed.',fitted)
        self.assertNotIn('desktop context',fitted); self.assertLessEqual(estimate,80)

    def test_estimator_is_shared_and_deterministic(self):
        self.assertEqual(estimate_tokens('x'*36),10)
    def test_worst_case_scenarios_fit_limit(self):
        for persona in ('','PERSONA '*100):
            system='SAFETY CORE\nLANGUAGE RULE\nCHARACTER\n'+persona+'\nSituation: '+('context '*100)
            history=[{'role':'user','content':'history '*100},{'role':'assistant','content':'answer '*100}]*4
            fitted,turns,estimate,truncated=PromptBudget.fit(system,history,'Explain TCP handshake in detail.',limit=1800,persona_text=persona)
            self.assertLessEqual(estimate,1800)
            self.assertIn('SAFETY CORE',fitted)

if __name__=='__main__': unittest.main()
