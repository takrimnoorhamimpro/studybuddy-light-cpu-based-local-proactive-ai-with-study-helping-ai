"""Deterministic chat commands for tasks, reminders and customizable timers."""
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _studybuddy_test_support import PROJECT_ROOT
from chat_core import ChatCore
from memory import Memory


class FakeLLM:
    def chat(self, messages):
        return "Okay, let's do that."


class TestProductivityCommands(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(":memory:")
        self.core = ChatCore(str(PROJECT_ROOT), llm=FakeLLM(), memory=self.memory)
        self.timers = []
        self.core.timer_callback = lambda seconds, label: self.timers.append((seconds, label))

    def tearDown(self):
        self.memory.close()

    def test_add_and_list_tasks(self):
        result = self.core.handle_message("Add task: finish my networking assignment")
        self.assertIn("Task added", result["reply"])
        self.assertEqual(self.memory.list_tasks()[0]["title"], "finish my networking assignment")
        result = self.core.handle_message("Show my tasks")
        self.assertIn("networking assignment", result["reply"])

    def test_add_to_task_colon_variant_is_deterministic(self):
        result = self.core.handle_message("ok can you please add to task : have to pee")
        self.assertIn("Task added", result["reply"])
        self.assertEqual(self.memory.list_tasks()[0]["title"], "have to pee")

    def test_complete_task_with_colon_before_title(self):
        self.core.handle_message("Add task: have to pee")
        result = self.core.handle_message("complete task : have to pee")
        self.assertIn("Marked 'have to pee' as complete", result["reply"])
        self.assertEqual(self.memory.list_tasks(), [])

    def test_mark_complete_hyphen_variant(self):
        self.core.handle_message("Add task: have to pee")
        result = self.core.handle_message("mark as complete - have to pee")
        self.assertIn("Marked 'have to pee' as complete", result["reply"])
        self.assertEqual(self.memory.list_tasks(), [])

    def test_complete_whatever_in_tasks_only_when_unique(self):
        self.core.handle_message("Add task: have to pee")
        result = self.core.handle_message("mark as complete whatever in my tasks")
        self.assertIn("Marked 'have to pee' as complete", result["reply"])
        self.assertEqual(self.memory.list_tasks(), [])

    def test_complete_whatever_in_tasks_does_not_complete_multiple(self):
        self.core.handle_message("Add task: have to pee")
        self.core.handle_message("Add task: drink water")
        result = self.core.handle_message("mark as complete whatever in my tasks")
        self.assertIn("multiple open tasks", result["reply"].lower())
        self.assertEqual(len(self.memory.list_tasks()), 2)

    def test_complete_this_task_uses_recent_local_reference(self):
        added = self.core.handle_message("Add task: finish physics lab report")
        self.assertIn("Task added", added["reply"])
        completed = self.core.handle_message("Complete this task")
        self.assertIn("Marked 'finish physics lab report' as complete", completed["reply"])
        self.assertEqual(self.memory.list_tasks(), [])

    def test_partial_task_match_does_not_complete_multiple_items(self):
        self.core.handle_message("Add task: prepare physics report")
        self.core.handle_message("Add task: prepare physics presentation")
        reply = self.core.handle_message("Complete task prepare physics")
        self.assertIn("more than one matching open task", reply["reply"].lower())
        self.assertEqual(len(self.memory.list_tasks()), 2)

    def test_custom_timer_is_persisted_and_callback_invoked(self):
        result = self.core.handle_message("Start a timer for 1 hour 20 minutes")
        self.assertIn("Timer started", result["reply"])
        self.assertEqual(self.timers, [(4800, "Focus timer")])
        reminders = self.memory.list_reminders()
        self.assertEqual(len(reminders), 1)
        self.assertIn("focus timer has finished", reminders[0]["text"])
        self.assertGreater(reminders[0]["remind_ts"], time.time() + 4700)

    def test_relative_reminder_with_hours(self):
        result = self.core.handle_message("Remind me in 2 hours to drink water")
        self.assertIn("Reminder saved", result["reply"])
        self.assertEqual(len(self.memory.list_reminders()), 1)
        self.assertGreater(self.memory.list_reminders()[0]["remind_ts"], time.time() + 7100)

    def test_reminder_in_days(self):
        result = self.core.handle_message("Remind me to submit the form in 2 days")
        self.assertIn("Reminder saved", result["reply"])
        self.assertGreater(self.memory.list_reminders()[0]["remind_ts"], time.time() + 170000)

    def test_pending_reminder_does_not_capture_unrelated_question(self):
        self.core.handle_message("set a reminder for me for 2 min")
        self.assertIsNotNone(self.core.pending_reminder_seconds)
        result = self.core.handle_message("Can you explain why hydration matters?")
        self.assertIn("Okay, let's do that.", result["reply"])
        self.assertEqual(self.memory.list_reminders(), [])
        self.assertIsNotNone(self.core.pending_reminder_seconds)

    def test_incomplete_reminder_request_asks_followup_and_saves_next_message(self):
        result = self.core.handle_message("set a reminder for me for 2 min")
        self.assertIn("What should I remind you about?", result["reply"])
        self.assertEqual(self.memory.list_reminders(), [])
        result = self.core.handle_message("drink water")
        self.assertIn("Reminder saved", result["reply"])
        reminders = self.memory.list_reminders()
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "drink water")
        self.assertGreater(reminders[0]["remind_ts"], time.time() + 110)

    def test_reminder_variant_with_task_in_same_message(self):
        result = self.core.handle_message("set a reminder for me for 2 min to stretch")
        self.assertIn("Reminder saved", result["reply"])
        self.assertEqual(self.memory.list_reminders()[0]["text"], "stretch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
