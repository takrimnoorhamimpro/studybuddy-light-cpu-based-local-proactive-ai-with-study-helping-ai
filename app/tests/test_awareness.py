import os
import sys
import unittest
from unittest.mock import patch
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from context_awareness import _category, WindowsContextMonitor
from initiative import InitiativeEngine

class FakeCore:
    class LLM:
        def __init__(self): self.calls = 0
        def chat(self, messages):
            self.calls += 1
            return "Hey, what are you up to over there?"
    def __init__(self): self.llm = self.LLM()

class FakeMonitor:
    supported = True
    def __init__(self, snap): self.snap = snap
    def snapshot(self): return dict(self.snap)
    def situation_line(self, s): return "category: " + s["category"]

class TestAwareness(unittest.TestCase):
    def test_categories_are_app_name_based_not_claimed_content(self):
        self.assertEqual(_category("Code.exe"), "Coding")
        self.assertEqual(_category("Spotify.exe"), "Music")
        self.assertEqual(_category("bank-login.exe"), "Private (hidden)")
        self.assertEqual(_category("chrome.exe"), "Browser")

    def test_private_context_is_never_nudged(self):
        core = FakeCore()
        monitor = FakeMonitor({"supported": True, "private": True, "meeting": False, "category": "Private (hidden)", "idle_seconds": 500, "app_seconds": 900, "audio_apps": []})
        engine = InitiativeEngine(core, monitor, {"proactive_app_seconds": 1, "proactive_stability_seconds": 0})
        self.assertIsNone(engine.check_once())
        self.assertEqual(core.llm.calls, 0)

    def test_meeting_is_never_nudged(self):
        core = FakeCore()
        monitor = FakeMonitor({"supported": True, "private": False, "meeting": True, "category": "Meeting / call", "idle_seconds": 500, "app_seconds": 900, "audio_apps": []})
        engine = InitiativeEngine(core, monitor, {"proactive_app_seconds": 1, "proactive_stability_seconds": 0})
        self.assertIsNone(engine.check_once())
        self.assertEqual(core.llm.calls, 0)

    def test_needs_stable_context_before_nudge(self):
        core = FakeCore()
        monitor = FakeMonitor({"supported": True, "private": False, "meeting": False, "category": "Coding", "idle_seconds": 500, "app_seconds": 900, "audio_apps": []})
        engine = InitiativeEngine(core, monitor, {"proactive_app_seconds": 1, "proactive_stability_seconds": 0})
        self.assertIsNone(engine.check_once())
        self.assertEqual(engine.check_once(), "Hey, what are you up to over there?")
        self.assertEqual(core.llm.calls, 1)
        self.assertIsNone(engine.check_once())

if __name__ == "__main__": unittest.main()
