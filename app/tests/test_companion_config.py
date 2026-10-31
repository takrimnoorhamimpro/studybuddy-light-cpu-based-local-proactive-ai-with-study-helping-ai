import os,sys,tempfile,unittest
sys.path.insert(0,os.path.dirname(__file__))
from companion.config import load_config

class CompanionConfigTests(unittest.TestCase):
    def test_missing_and_invalid_config_use_safe_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            missing=load_config(os.path.join(folder,'missing.json'))
            self.assertEqual(missing['prompt_token_limit'],1800)
            path=os.path.join(folder,'bad.json')
            with open(path,'w',encoding='utf8') as f:f.write('{bad json')
            invalid=load_config(path)
            self.assertEqual(invalid['response_modes'],{'quick':48,'chat':88,'deep':144})
    def test_malformed_tunables_are_clamped(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'companion.json')
            with open(path,'w',encoding='utf8') as f:f.write('{"prompt_token_limit":99999,"max_injected_items":-4,"response_modes":{"quick":"bad","chat":10000},"trigger_cooldown_minutes":"bad","trigger_base_value":7,"initiative_quiet_default":"not-a-time"}')
            config=load_config(path)
            self.assertEqual(config['prompt_token_limit'],1800)
            self.assertEqual(config['max_injected_items'],0)
            self.assertEqual(config['response_modes']['quick'],48)
            self.assertEqual(config['response_modes']['chat'],512)
            self.assertEqual(config['trigger_cooldown_minutes']['usual_study_start'],1380)
            self.assertEqual(config['trigger_base_value']['open_loop_due'],1.0)
            self.assertEqual(config['initiative_quiet_default'],'23:00-07:00')

if __name__=='__main__':unittest.main()
