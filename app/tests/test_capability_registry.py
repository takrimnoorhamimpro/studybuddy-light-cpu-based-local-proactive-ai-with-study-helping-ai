import os
import unittest
from _studybuddy_test_support import PROJECT_ROOT

from intelligence.capability_registry import Capability, CapabilityRegistry
from chat_core import ChatCore


class CapabilityRegistryTests(unittest.TestCase):
    def test_snapshot_reports_live_state(self):
        registry = CapabilityRegistry()
        registry.register(Capability(
            "demo", "Demo", "Test", "Example capability",
            lambda: {"status": "active", "detail": "running"},
        ))
        item = registry.snapshot()[0]
        self.assertEqual(item["id"], "demo")
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["detail"], "running")

    def test_provider_failure_is_visible(self):
        def broken():
            raise RuntimeError("offline")
        registry = CapabilityRegistry()
        registry.register(Capability("broken", "Broken", "Test", "Failure", broken))
        self.assertEqual(registry.snapshot()[0]["status"], "error")

    def test_direct_capability_questions_use_registry_not_model(self):
        class NeverCallModel:
            def chat(self, _messages):
                raise AssertionError("The LLM must not invent its own capabilities")
        base_dir = str(PROJECT_ROOT)
        core = ChatCore(base_dir, llm=NeverCallModel(), memory=None)
        core.capability_list_provider = lambda: "• Tasks — ready\n• Camera — disabled"
        result = core.handle_message("What built-in commands can you do?")
        self.assertEqual(result["flag"], "capabilities")
        self.assertIn("Tasks — ready", result["reply"])
        self.assertIn("Camera — disabled", result["reply"])

    def test_memory_access_question_uses_local_memory_state_not_model(self):
        from memory import Memory
        class NeverCallModel:
            def chat(self, _messages):
                raise AssertionError("Memory access questions should be answered from app state")
        base_dir = str(PROJECT_ROOT)
        memory = Memory(":memory:")
        try:
            core = ChatCore(base_dir, llm=NeverCallModel(), memory=memory)
            result = core.handle_message("Can you access your memory?")
            self.assertEqual(result["flag"], "memory_access")
            self.assertIn("local memory database", result["reply"])
            self.assertIn("conversation turns", result["reply"])
            self.assertIn("other apps", result["reply"])
        finally:
            memory.close()


if __name__ == "__main__":
    unittest.main()
