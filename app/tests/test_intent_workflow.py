"""Regression tests for editable keyword routing and stateful confirmation workflows."""
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
from _studybuddy_test_support import PROJECT_ROOT
ROOT = str(PROJECT_ROOT)
sys.path.insert(0, HERE)
from chat_core import ChatCore
from intent_router import IntentRouter
from memory import Memory


class FakeLLM:
    def __init__(self, reply="Normal chat response"):
        self.reply = reply
        self.calls = 0
        self.last_messages = None

    def chat(self, messages):
        self.calls += 1
        self.last_messages = messages
        return self.reply


class IntentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(":memory:")
        self.llm = FakeLLM()
        self.core = ChatCore(ROOT, llm=self.llm, memory=self.memory)
        self.timer_calls = []
        self.core.timer_callback = lambda seconds, label: self.timer_calls.append((seconds, label))

    def tearDown(self):
        self.memory.close()

    def test_dictionary_is_external_and_detects_ambiguous_task_add(self):
        path = os.path.join(ROOT, "config", "intent_dictionary.json")
        self.assertTrue(os.path.isfile(path))
        candidate = self.core.intent_router.detect("I need to add a task")
        self.assertEqual(candidate["id"], "add_task")
        response = self.core.handle_message("I need to add a task")
        self.assertIn("Reply YES or NO", response["reply"])
        self.assertEqual(self.llm.calls, 0)

    def test_direct_add_task_bypasses_confirmation_even_if_title_says_finish(self):
        response = self.core.handle_message("Add task: finish assignment")
        self.assertIn("Task added", response["reply"])
        self.assertEqual(self.memory.list_tasks()[0]["title"], "finish assignment")
        self.assertEqual(self.llm.calls, 0)

    def test_add_the_task_variant_is_supported(self):
        response = self.core.handle_message("Can you please add the task have to pee")
        self.assertIn("Task added", response["reply"])
        self.assertEqual(self.memory.list_tasks()[0]["title"], "have to pee")

    def test_yes_to_add_task_collects_title_and_saves(self):
        first = self.core.handle_message("I need to add a task")
        self.assertIn("YES or NO", first["reply"])
        second = self.core.handle_message("yes")
        self.assertIn("What task should I add", second["reply"])
        third = self.core.handle_message("Finish physics assignment")
        self.assertIn("Task added", third["reply"])
        self.assertEqual(self.memory.list_tasks()[0]["title"], "Finish physics assignment")
        self.assertFalse(self.core.intent_router.has_pending)

    def test_no_declines_action_and_uses_llm_without_tool_execution(self):
        self.core.handle_message("I need to add a task")
        result = self.core.handle_message("no")
        self.assertEqual(result["flag"], "intent_declined")
        self.assertEqual(self.llm.calls, 1)
        self.assertEqual(self.memory.list_tasks(), [])
        # The original request is included in the no-tool system context.
        content = "\n".join(m.get("content", "") for m in (self.llm.last_messages or []))
        self.assertIn("I need to add a task", content)
        self.assertIn("Do not execute tools", content)

    def test_complete_task_workflow_lists_and_completes_selected_id(self):
        self.memory.add_task("Finish report")
        self.memory.add_task("Revise chapter 3")
        first = self.core.handle_message("task complete")
        self.assertIn("YES or NO", first["reply"])
        second = self.core.handle_message("yes")
        self.assertIn("1. Revise chapter 3", second["reply"])
        self.assertIn("2. Finish report", second["reply"])
        third = self.core.handle_message("1")
        self.assertIn("Marked 'Revise chapter 3' as complete", third["reply"])
        self.assertEqual([t["title"] for t in self.memory.list_tasks()], ["Finish report"])
        completed = [t for t in self.memory.list_tasks(include_done=True) if t["status"] == "done"]
        self.assertEqual([t["title"] for t in completed], ["Revise chapter 3"])

    def test_complete_task_workflow_requires_selection_when_multiple(self):
        self.memory.add_task("One task")
        self.memory.add_task("Another task")
        self.core.handle_message("task complete")
        self.core.handle_message("yes")
        result = self.core.handle_message("task")
        self.assertIn("couldn't match", result["reply"].lower())
        self.assertEqual(len(self.memory.list_tasks()), 2)

    def test_reminder_workflow_collects_task_then_time(self):
        first = self.core.handle_message("I need a reminder")
        self.assertIn("YES or NO", first["reply"])
        second = self.core.handle_message("yes")
        self.assertIn("What should I remind you about", second["reply"])
        third = self.core.handle_message("have to pee")
        self.assertIn("When should I remind you", third["reply"])
        fourth = self.core.handle_message("in 20 minutes")
        self.assertIn("Reminder saved", fourth["reply"])
        reminders = self.memory.list_reminders()
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "have to pee")
        self.assertGreater(reminders[0]["remind_ts"], time.time() + 110)


    def test_reminder_capability_statement_is_not_a_reminder_request(self):
        result = self.core.handle_message("you can set reminders also")
        self.assertEqual(result["flag"], "none")
        self.assertFalse(self.core.intent_router.has_pending)
        self.assertEqual(self.memory.list_reminders(), [])

    def test_hazardous_reminder_is_rejected_and_dialog_clears(self):
        self.core.handle_message("I need a reminder")
        self.core.handle_message("yes")
        result = self.core.handle_message("Remind me in 20 seconds to drink blood")
        self.assertIn("can't schedule a reminder", result["reply"].casefold())
        self.assertFalse(self.core.intent_router.has_pending)
        self.assertEqual(self.memory.list_reminders(), [])
        self.assertIsNone(self.core.pending_reminder_seconds)

    def test_timer_candidate_confirmation_and_duration(self):
        first = self.core.handle_message("I want a timer")
        self.assertIn("YES or NO", first["reply"])
        second = self.core.handle_message("yes")
        self.assertIn("How long", second["reply"])
        third = self.core.handle_message("25 minutes")
        self.assertIn("Timer started", third["reply"])
        self.assertEqual(self.timer_calls, [(1500, "Focus timer")])

    def test_educational_and_negated_mentions_do_not_trigger_workflow(self):
        for text in ("What is a reminder?", "I don't need a reminder", "How do I complete a task?"):
            self.assertIsNone(self.core.intent_router.detect(text), text)

    def test_dictionary_yes_no_phrase_edit_is_loaded(self):
        path = os.path.join(ROOT, "config", "intent_dictionary.json")
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertIn("intent_dictionary.json", self.core.intent_router.dictionary_path)
        self.assertIn("yeah", config["yes_phrases"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
