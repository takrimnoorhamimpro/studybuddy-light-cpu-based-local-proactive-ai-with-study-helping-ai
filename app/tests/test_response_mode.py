import os,sys,unittest
from unittest.mock import patch,Mock
sys.path.insert(0,os.path.dirname(__file__))
from companion.response_mode import choose_mode
from llm import OllamaClient

class ResponseModeTests(unittest.TestCase):
    def test_mode_selection(self):
        self.assertEqual(choose_mode('hello'),'quick')
        self.assertEqual(choose_mode('yes'),'quick')
        self.assertEqual(choose_mode('How does TCP handshake work?'),'deep')
        self.assertEqual(choose_mode('Tell me more about networking'),'chat')
    def test_per_call_num_predict_reaches_client_without_changing_global_options(self):
        client=OllamaClient(options={'num_gpu':0,'num_thread':4,'num_ctx':3072,'num_predict':64})
        response=Mock(status_code=200); response.json.return_value={'message':{'role':'assistant','content':'ok'},'done_reason':'stop'}
        with patch('llm.requests.post',return_value=response) as post:
            client.chat_completion([{'role':'user','content':'explain'}],options={'num_predict':170,'num_gpu':8})
        body=post.call_args.kwargs['json']
        self.assertEqual(body['options']['num_predict'],170); self.assertEqual(body['options']['num_gpu'],0)
        self.assertEqual(client.options['num_predict'],64); self.assertEqual(client.options['num_gpu'],0)

if __name__=='__main__': unittest.main()
