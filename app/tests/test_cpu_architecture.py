import json
import os
import unittest
from _studybuddy_test_support import PROJECT_ROOT

ROOT = str(PROJECT_ROOT)


class CPUArchitectureTests(unittest.TestCase):
    def test_primary_runtime_uses_one_llama_model_and_direct_tool_catalogue(self):
        with open(os.path.join(ROOT, "config", "settings.json"), encoding="utf-8") as file:
            settings = json.load(file)
        self.assertEqual(settings["model_chat"], "llama3.2:3b")
        self.assertEqual(settings["tool_retrieval_mode"], "bm25")
        self.assertEqual(settings["ollama_options"]["num_gpu"], 0)
        self.assertLessEqual(settings["ollama_options"]["num_ctx"], 3072)
        self.assertLessEqual(settings["tool_retrieval_max_tools"], 4)
        self.assertEqual(settings["history_restore_hours"], 0)
        self.assertEqual(settings["retrieved_notes"], 0)
        self.assertEqual(settings["max_prompt_facts"], 0)
        self.assertEqual(settings["model_keep_alive_mode"], "Unlimited")
        self.assertNotIn("model_embed", settings)


    def test_normal_chat_does_not_get_all_fourteen_tool_schemas(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "app"))
        from chat_core import ChatCore
        from agent.tool_orchestrator import ToolOrchestrator
        from agent.builtin_tools import build_builtin_tool_registry
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        class Fake:
            def chat_completion(self, messages, tools=None, on_token=None):
                self.tools = tools
                return {"message": {"role": "assistant", "content": "Thanks for sharing."}}
        fake = Fake()
        # A real ChatCore isn't needed for registry descriptions; supply a minimal fake core.
        class Core:
            memory = None
        registry = build_builtin_tool_registry(Core())
        agent = ToolOrchestrator(fake, registry, retrieval_mode="bm25",
                                 routing_config_path=os.path.join(ROOT, "config", "intent_dictionary.json"))
        self.assertEqual(agent.tools_for_text("I am also fine thanks for asking"), [])
        self.assertEqual(agent.tools_for_text("I need to pee"), [])
        self.assertEqual(agent.tools_for_text("I need to task"), [])
        self.assertEqual(agent.tools_for_text("I should remember to drink water"), [])
        self.assertEqual(agent.tools_for_text("show my tasks"), ["list_tasks"])
        self.assertEqual(agent.tools_for_text("remind me in 20 seconds to drink water"), ["create_reminder"])

if __name__ == "__main__":
    unittest.main()
