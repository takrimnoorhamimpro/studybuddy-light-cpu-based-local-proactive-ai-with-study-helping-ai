"""Tests the flexible local-LLM intent bridge for natural-language utilities."""
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _studybuddy_test_support import PROJECT_ROOT
from chat_core import ChatCore
from memory import Memory


class IntentLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return json.dumps(self.payload)


class TestSmartUtilityCommands(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(":memory:")

    def tearDown(self):
        self.memory.close()

    def core(self, payload):
        core = ChatCore(str(PROJECT_ROOT), llm=IntentLLM(payload), memory=self.memory)
        self.timer_calls = []
        core.timer_callback = lambda seconds, label: self.timer_calls.append((seconds, label))
        return core

    def test_varied_note_wording_saves_note(self):
        core = self.core({"intent": "add_note", "content": "My lab report is due Friday", "seconds": 0, "when": "", "title": ""})
        result = core.handle_message("Jot down that my lab report is due Friday")
        self.assertIn("Saved a note", result["reply"])
        self.assertIn("lab report is due Friday", self.memory.list_notes()[0]["text"])

    def test_varied_timer_wording_starts_timer(self):
        core = self.core({"intent": "start_timer", "content": "", "seconds": 2220, "when": "", "title": ""})
        result = core.handle_message("Give me a 37-minute focus session")
        self.assertIn("Timer started", result["reply"])
        self.assertEqual(self.timer_calls, [(2220, "Focus timer")])

    def test_varied_reminder_wording_saves_reminder(self):
        core = self.core({"intent": "add_reminder", "content": "drink water", "seconds": 120, "when": "", "title": ""})
        result = core.handle_message("Could you give me a nudge in two minutes about drinking water?")
        self.assertIn("Reminder saved", result["reply"])
        self.assertEqual(self.memory.list_reminders()[0]["text"], "drink water")
        self.assertGreater(self.memory.list_reminders()[0]["remind_ts"], time.time() + 110)


if __name__ == "__main__":
    unittest.main(verbosity=2)
