"""Regression coverage for v9: ordinary chat, memory isolation, task counts, context."""
import json
import os
import sys
import unittest
from unittest.mock import patch
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from chat_core import ChatCore
from memory import Memory
from context_engine import ContextEngine, ContextSnapshot
from agent.tool_orchestrator import ToolOrchestrator
from agent.builtin_tools import build_builtin_tool_registry

from _studybuddy_test_support import PROJECT_ROOT
ROOT = str(PROJECT_ROOT)

class FakeLLM:
    def __init__(self, reply="Okay."):
        self.reply = reply
        self.calls = []
    def chat(self, messages):
        self.calls.append(messages)
        return self.reply
    def chat_completion(self, messages, tools=None, on_token=None):
        self.calls.append({"messages": messages, "tools": tools})
        if callable(on_token):
            on_token(self.reply)
        return {"message": {"role": "assistant", "content": self.reply}}

class V9ChatIsolationTests(unittest.TestCase):
    def test_common_phrases_are_not_routed_to_tools_or_turned_into_tasks(self):
        memory = Memory(":memory:")
        fake = FakeLLM("Thanks for sharing.")
        try:
            core = ChatCore(ROOT, llm=fake, memory=memory)
            core.tool_registry = build_builtin_tool_registry(core)
            core.tool_orchestrator = ToolOrchestrator(
                fake, core.tool_registry, retrieval_mode="bm25",
                routing_config_path=os.path.join(ROOT, "config", "intent_dictionary.json"))
            for text in ("I need to pee", "I need to task", "I'm also fine thanks for asking",
                         "I am watching Netflix", "I should remember to drink water"):
                core.handle_message(text)
            self.assertEqual(memory.list_tasks(), [])
            self.assertEqual(memory.list_notes(10), [])
            self.assertTrue(all((call.get("tools") or []) == [] for call in fake.calls if isinstance(call, dict)))
        finally:
            memory.close()

    def test_old_chat_history_is_not_restored_at_startup(self):
        memory = Memory(":memory:")
        try:
            memory.add_turn("user", "We watched God of War yesterday")
            memory.add_turn("assistant", "You were watching God of War")
            core = ChatCore(ROOT, llm=FakeLLM("Hi"), memory=memory)
            self.assertEqual(core.history, [])
        finally:
            memory.close()

    def test_old_saved_note_is_not_injected_into_ordinary_chat_prompt(self):
        memory = Memory(":memory:")
        fake = FakeLLM("Okay.")
        try:
            memory.add_note("manual", "2026-10-27", "User was watching an unrelated retro game video")
            core = ChatCore(ROOT, llm=fake, memory=memory)
            core.handle_message("I'm good, thanks")
            prompt = "\n".join(m.get("content", "") for m in fake.calls[0])
            self.assertNotIn("retro game video", prompt)
        finally:
            memory.close()

    def test_operational_turn_is_persisted_but_not_kept_as_smalltalk_context(self):
        memory = Memory(":memory:")
        try:
            core = ChatCore(ROOT, llm=FakeLLM("Okay"), memory=memory)
            core._remember("Remind me in 20 seconds to drink water", "Reminder saved: I'll remind you to drink water in 20 seconds.")
            self.assertEqual(core.history, [])
            self.assertGreaterEqual(len(memory.recent_turns(4)), 2)
        finally:
            memory.close()


    def test_old_reminder_text_does_not_bleed_into_next_smalltalk_prompt(self):
        memory = Memory(":memory:")
        fake = FakeLLM("No worries!")
        try:
            memory.add_turn("user", "Remind me in 20 seconds to drink blood")
            memory.add_turn("assistant", "Reminder saved: I'll remind you to drink blood in 20 seconds.")
            core = ChatCore(ROOT, llm=fake, memory=memory)
            core.handle_message("ok thanks")
            prompt = "\n".join(str(m.get("content", "")) for m in fake.calls[0])
            self.assertNotIn("drink blood", prompt.casefold())
            self.assertNotIn("God of War", prompt)
        finally:
            memory.close()

    def test_task_count_reports_total_open_and_completed(self):
        memory = Memory(":memory:")
        try:
            core = ChatCore(ROOT, llm=FakeLLM(), memory=memory)
            first = memory.add_task("finish assignment")
            memory.add_task("revise chapter")
            memory.complete_task(first)
            answer = core._handle_productivity_command("show how many tasks I have had", allow_smart=False)
            self.assertIn("2 tasks altogether", answer)
            self.assertIn("1 open", answer)
            self.assertIn("1 completed", answer)
            done = core._handle_productivity_command("what was the task I completed", allow_smart=False)
            self.assertIn("finish assignment", done)
            self.assertNotIn("revise chapter", done)
        finally:
            memory.close()


    def test_expired_pending_reminder_does_not_capture_ordinary_chat(self):
        import time
        memory = Memory(":memory:")
        fake = FakeLLM("Thanks for telling me.")
        try:
            core = ChatCore(ROOT, llm=fake, memory=memory)
            core.pending_reminder_seconds = 20
            core.pending_reminder_created_at = time.time() - 301
            result = core.handle_message("I need to pee")
            self.assertEqual(result["reply"], "Thanks for telling me.")
            self.assertEqual(memory.list_reminders(), [])
            self.assertIsNone(core.pending_reminder_seconds)
        finally:
            memory.close()

    def test_tool_created_pending_reminder_gets_an_expiry_timestamp(self):
        from agent.builtin_tools import build_builtin_tool_registry
        memory = Memory(":memory:")
        try:
            core = ChatCore(ROOT, llm=FakeLLM(), memory=memory)
            registry = build_builtin_tool_registry(core)
            result = registry.execute("create_reminder", {"delay_seconds": 20})
            self.assertTrue(result.get("needs_followup"))
            self.assertIsNotNone(core.pending_reminder_created_at)
        finally:
            memory.close()

    def test_questioned_name_does_not_overwrite_profile(self):
        import identity
        self.assertEqual(identity.extract_names("My name is Pixel? When did I set it up haha"), (None, None))
        self.assertEqual(identity.extract_names("My name is Takrim"), ("Takrim", None))


    def test_user_profile_tool_does_not_report_assistant_name_as_user_fact(self):
        from agent.builtin_tools import build_builtin_tool_registry
        memory = Memory(":memory:")
        try:
            memory.set_profile("user_name", "Takrim")
            memory.set_profile("buddy_name", "Pixel")
            core = ChatCore(ROOT, llm=FakeLLM(), memory=memory)
            profile = build_builtin_tool_registry(core).execute("get_user_profile", {})
            self.assertEqual(profile, {"user_name": "Takrim"})
            self.assertNotIn("buddy_name", profile)
        finally:
            memory.close()

    def test_explicit_name_correction_overrides_old_wrong_profile(self):
        import identity
        self.assertEqual(identity.extract_names("I am Takrim, not Pixel"), ("Takrim", None))
        memory = Memory(":memory:")
        try:
            core = ChatCore(ROOT, llm=FakeLLM(), memory=memory)
            memory.set_profile("user_name", "Pixel")
            core.user_name = "Pixel"
            result = core.handle_message("I am Takrim, not Pixel")
            self.assertEqual(core.user_name, "Takrim")
            self.assertEqual(memory.get_profile("user_name"), "Takrim")
        finally:
            memory.close()


    def test_legacy_wrong_user_name_is_repaired_from_explicit_correction_only(self):
        memory = Memory(":memory:")
        try:
            memory.set_profile("user_name", "Pixel")
            memory.add_turn("user", "Hey, I am Takrim, not Pixel")
            memory.add_turn("assistant", "Saved your corrected profile name.")
            core = ChatCore(ROOT, llm=FakeLLM(), memory=memory)
            self.assertEqual(core.user_name, "Takrim")
            self.assertEqual(memory.get_profile("user_name"), "Takrim")
            self.assertEqual(core.history, [])  # migration must not restore the transcript
        finally:
            memory.close()


    def test_latest_explicit_name_correction_wins_over_older_correction(self):
        memory = Memory(":memory:")
        try:
            memory.set_profile("user_name", "Pixel")
            memory.add_turn("user", "I am Takrim, not Pixel")
            memory.add_turn("assistant", "Okay")
            memory.add_turn("user", "I am Pixel, not Takrim")
            core = ChatCore(ROOT, llm=FakeLLM(), memory=memory)
            self.assertEqual(core.user_name, "Pixel")
        finally:
            memory.close()

    def test_current_desktop_snapshot_cache_never_keeps_private_window(self):
        engine = ContextEngine()
        external = ContextSnapshot(app_category="Media", app_name="chrome", window_title="YouTube - Lecture title", captured_at=100)
        own = ContextSnapshot(app_category="Other", app_name="python", window_title="Study Buddy", captured_at=110)
        with patch.object(ContextEngine, "capture", side_effect=[external, own]):
            engine.observe()
            engine.observe()
        cached = engine.last_external_summary(max_age_seconds=1000)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["observation_source"], "last_external_window")
        self.assertEqual(cached["window_title"], "YouTube - Lecture title")
        self.assertGreaterEqual(cached["age_seconds"], 0)

    def test_proactive_message_is_not_added_to_core_history_by_ui_contract(self):
        # The GUI now assigns pending_proactive_context, not core.history. This asserts
        # the one-shot field exists and that operational persistence remains separate.
        core = ChatCore(ROOT, llm=FakeLLM())
        core.pending_proactive_context = "Looks like the title mentions Newton's laws."
        core._active_proactive_context = core.pending_proactive_context
        core.pending_proactive_context = None
        self.assertEqual(core.history, [])
        self.assertIsNotNone(core._active_proactive_context)

if __name__ == "__main__":
    unittest.main(verbosity=2)
