import unittest
from llm import resolve_keep_alive


class KeepAliveTests(unittest.TestCase):
    def test_default_is_thirty_minutes(self):
        self.assertEqual(resolve_keep_alive({}), "30m")

    def test_one_hour(self):
        self.assertEqual(resolve_keep_alive({"model_keep_alive_mode": "1 hour"}), "1h")

    def test_custom_minutes(self):
        self.assertEqual(resolve_keep_alive({"model_keep_alive_mode": "Custom", "model_keep_alive_custom_minutes": 45}), "45m")

    def test_custom_minutes_are_bounded(self):
        self.assertEqual(resolve_keep_alive({"model_keep_alive_mode": "Custom", "model_keep_alive_custom_minutes": 0}), "1m")
        self.assertEqual(resolve_keep_alive({"model_keep_alive_mode": "Custom", "model_keep_alive_custom_minutes": 99999}), "10080m")

    def test_unlimited(self):
        self.assertEqual(resolve_keep_alive({"model_keep_alive_mode": "Unlimited"}), -1)


if __name__ == "__main__":
    unittest.main()


class TestOllamaEmbeddings(unittest.TestCase):
    def test_embed_uses_local_ollama_endpoint_and_returns_vectors(self):
        from unittest.mock import Mock, patch
        from llm import OllamaClient
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"model": "nomic-embed-text", "embeddings": [[0.1, 0.2], [0.3, 0.4]],
                                      "load_duration": 1000000, "prompt_eval_count": 4}
        with patch("llm.requests.post", return_value=response) as post:
            client = OllamaClient(embedding_model="nomic-embed-text")
            vectors = client.embed(["first text", "second text"])
        self.assertEqual(vectors, [[0.1, 0.2], [0.3, 0.4]])
        self.assertTrue(post.call_args.args[0].endswith("/api/embed"))
        self.assertEqual(post.call_args.kwargs["json"]["model"], "nomic-embed-text")
        self.assertEqual(post.call_args.kwargs["json"]["input"], ["first text", "second text"])


class TestOllamaMissingModelRecovery(unittest.TestCase):
    def test_llama_alias_retries_with_installed_3b_tag(self):
        from unittest.mock import Mock, patch
        from llm import OllamaClient
        missing = Mock(); missing.status_code = 404; missing.text = "{\"error\":\"model 'llama3.2' not found\"}"
        ok = Mock(); ok.status_code = 200
        ok.json.return_value = {"model": "llama3.2:3b", "message": {"role": "assistant", "content": "ready"},
                                "prompt_eval_count": 1, "eval_count": 1, "eval_duration": 1000000}
        tags = Mock(); tags.status_code = 200; tags.json.return_value = {"models": [{"name": "llama3.2:3b"}]}
        with patch("llm.requests.post", side_effect=[missing, ok]) as post, \
             patch("llm.requests.get", return_value=tags):
            client = OllamaClient(model="llama3.2")
            result = client.chat_completion([{"role": "user", "content": "hello"}])
        self.assertEqual(result["message"]["content"], "ready")
        self.assertEqual(client.model, "llama3.2:3b")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs["json"]["model"], "llama3.2:3b")

    def test_missing_model_error_gives_actionable_command(self):
        from unittest.mock import Mock, patch
        from llm import LLMError, OllamaClient
        missing = Mock(); missing.status_code = 404; missing.text = "model not found"
        tags = Mock(); tags.status_code = 200; tags.json.return_value = {"models": [{"name": "qwen2.5:1.5b-instruct-q4_K_M"}]}
        with patch("llm.requests.post", return_value=missing), patch("llm.requests.get", return_value=tags):
            client = OllamaClient(model="llama3.2:3b")
            with self.assertRaises(LLMError) as caught:
                client.chat_completion([{"role": "user", "content": "hello"}])
        self.assertIn("ollama pull llama3.2:3b", str(caught.exception))
        self.assertIn("ollama list", str(caught.exception))


class TestOllamaStreaming(unittest.TestCase):
    class StreamResponse:
        status_code = 200
        text = ""

        def __init__(self, rows):
            self.rows = rows
            self.closed = False

        def iter_lines(self):
            return iter(self.rows)

        def close(self):
            self.closed = True

    def test_streaming_forwards_chunks_and_returns_complete_message(self):
        import json
        from unittest.mock import patch
        from llm import OllamaClient
        rows = [
            json.dumps({"model": "llama3.2:3b", "message": {"role": "assistant", "content": "Hello"}, "done": False}).encode(),
            json.dumps({"model": "llama3.2:3b", "message": {"role": "assistant", "content": " there."},
                        "done": True, "prompt_eval_count": 8, "eval_count": 3, "eval_duration": 100000000}).encode(),
        ]
        response = self.StreamResponse(rows)
        chunks = []
        with patch("llm.requests.post", return_value=response) as post:
            client = OllamaClient(model="llama3.2:3b")
            data = client.chat_completion([{"role": "user", "content": "hello"}], tools=[], on_token=chunks.append)
        self.assertEqual(chunks, ["Hello", " there."])
        self.assertEqual(data["message"]["content"], "Hello there.")
        self.assertTrue(post.call_args.kwargs["json"]["stream"])
        self.assertTrue(response.closed)
        self.assertEqual(client.last_metrics["generated_tokens"], 3)


    def test_streaming_preserves_native_tool_calls(self):
        import json
        from unittest.mock import patch
        from llm import OllamaClient
        tool_call = {"function": {"name": "get_desktop_context", "arguments": {}}}
        rows = [
            json.dumps({"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]},
                        "done": True, "done_reason": "tool_calls"}).encode(),
        ]
        response = self.StreamResponse(rows)
        with patch("llm.requests.post", return_value=response):
            client = OllamaClient(model="llama3.2:3b")
            data = client.chat_completion([{"role": "user", "content": "what is on my tab"}],
                                          tools=[{"type": "function", "function": {"name": "get_desktop_context"}}],
                                          on_token=lambda _chunk: True)
        self.assertEqual(data["message"]["tool_calls"], [tool_call])
        self.assertEqual(data["done_reason"], "tool_calls")

    def test_streaming_callback_can_cancel_low_priority_generation(self):
        import json
        from unittest.mock import patch
        from llm import OllamaClient
        rows = [
            json.dumps({"message": {"role": "assistant", "content": "First"}, "done": False}).encode(),
            json.dumps({"message": {"role": "assistant", "content": " second"}, "done": False}).encode(),
        ]
        response = self.StreamResponse(rows)
        seen = []
        def stop_after_first(chunk):
            if chunk:
                seen.append(chunk)
                return False
            return True
        with patch("llm.requests.post", return_value=response):
            client = OllamaClient(model="llama3.2:3b")
            data = client.chat_completion([{"role": "user", "content": "draft"}], tools=[], on_token=stop_after_first)
        self.assertEqual(seen, ["First"])
        self.assertTrue(data.get("cancelled"))
        self.assertEqual(data["message"]["content"], "First")
        self.assertTrue(response.closed)

    def test_cancel_event_closes_only_its_active_stream(self):
        import json
        import threading
        from unittest.mock import patch
        from llm import OllamaClient
        rows = [json.dumps({"message": {"role": "assistant", "content": "First"}, "done": False}).encode()]
        response = self.StreamResponse(rows)
        cancel_event = threading.Event()
        client = OllamaClient(model="llama3.2:3b")

        def cancel_on_first_token(_chunk):
            cancel_event.set()
            self.assertTrue(client.cancel_active_request(cancel_event))
            return False

        with patch("llm.requests.post", return_value=response):
            data = client.chat_completion([{"role": "user", "content": "review"}], tools=[],
                                          on_token=cancel_on_first_token, cancel_event=cancel_event)
        self.assertTrue(data.get("cancelled"))
        self.assertTrue(response.closed)
        self.assertIsNone(client._active_response)

    def test_external_cancel_unblocks_a_waiting_stream(self):
        import threading
        import requests
        from unittest.mock import patch
        from llm import OllamaClient

        class WaitingResponse:
            status_code = 200
            text = ""

            def __init__(self):
                self.started = threading.Event()
                self.stopped = threading.Event()
                self.closed = False

            def iter_lines(self):
                self.started.set()
                self.stopped.wait(2)
                if self.closed:
                    raise requests.ConnectionError("stream closed by foreground chat")
                yield b'{"message":{"role":"assistant","content":"late"},"done":true}'

            def close(self):
                self.closed = True
                self.stopped.set()

        response = WaitingResponse()
        cancel_event = threading.Event()
        client = OllamaClient(model="llama3.2:3b")
        results = []

        with patch("llm.requests.post", return_value=response):
            worker = threading.Thread(target=lambda: results.append(client.chat_completion(
                [{"role": "user", "content": "background review"}], tools=[],
                on_token=lambda _chunk: not cancel_event.is_set(), cancel_event=cancel_event)))
            worker.start()
            self.assertTrue(response.started.wait(1), "stream did not start")
            cancel_event.set()
            self.assertTrue(client.cancel_active_request(cancel_event))
            worker.join(1)

        self.assertFalse(worker.is_alive(), "closing the response did not release the model lock")
        self.assertTrue(results[0].get("cancelled"))
        self.assertTrue(response.closed)
