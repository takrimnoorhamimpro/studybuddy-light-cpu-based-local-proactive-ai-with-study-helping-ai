import datetime
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from memory import Memory
from lifecycle import LifecycleService


class FakeLLM:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("simulated model failure")
        prompt = messages[-1]["content"]
        if "Reflect on this companion's interaction style" in prompt:
            return json.dumps({"field": "traits", "add": "patient with follow-up questions", "reflection": "The user appreciates patient conversation."})
        if "Combine these segment notes" in prompt:
            return "- The user discussed a current task and prefers clear, patient conversation."
        return "- The user discussed a current task.\n- The user prefers clear, patient conversation."


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "users", "hamim"), exist_ok=True)
        character = {
            "name": "Buddy",
            "traits": ["warm", "curious"],
            "values": ["honesty", "respect"],
            "likes": [],
            "dislikes": [],
            "speech_style": "friendly English",
            "disagreement": "respectful and direct",
            "never_change_rules": ["be honest", "respect privacy"]
        }
        with open(os.path.join(self.root, "config", "character.json"), "w", encoding="utf-8") as f:
            json.dump(character, f)
        self.memory = Memory(":memory:")
        self.llm = FakeLLM()
        self.service = LifecycleService(self.root, self.memory, self.llm)

    def tearDown(self):
        self.memory.close()
        self.temp.cleanup()

    def test_personality_versions_use_active_profile(self):
        service = LifecycleService(self.root, self.memory, self.llm, profile="student_a")
        self.assertEqual(service.version_dir, os.path.join(self.root, "users", "student_a", "personality_versions"))

    def test_daily_summary_saved_before_raw_turns_deleted(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        ts = datetime.datetime.combine(yesterday, datetime.time(12, 0)).timestamp()
        self.memory.add_turn("user", "I have a project deadline and prefer short reminders.")
        self.memory.add_turn("assistant", "I can remind you and keep it concise.")
        self.memory.db.execute("UPDATE turns SET ts=?", (ts,))
        self.memory.db.commit()

        result = self.service.consolidate_daily(today=datetime.date.today())
        self.assertIn(yesterday.isoformat(), result["days_summarized"])
        self.assertEqual(self.memory.list_turns(10), [])
        note = self.memory.notes_for_periods("daily", yesterday.isoformat(), yesterday.isoformat())
        self.assertEqual(len(note), 1)
        self.assertIn("current task", note[0]["text"])

    def test_manual_consolidation_includes_today(self):
        today = datetime.date.today()
        self.memory.add_turn("user", "Today I need a reminder for my project deadline.")
        self.memory.add_turn("assistant", "I can help you keep track of the deadline.")

        result = self.service.consolidate_daily(today=today, include_today=True)

        self.assertIn(today.isoformat(), result["days_summarized"])
        self.assertEqual(result["turns_deleted"], 2)
        note = self.memory.notes_for_periods("daily", today.isoformat(), today.isoformat())
        self.assertEqual(len(note), 1)
        self.assertEqual(self.memory.list_turns(10), [])

    def test_auto_consolidation_only_deletes_turns_older_than_three_days(self):
        import time
        old_ts = time.time() - 4 * 86400
        self.memory.add_turn("user", "Four days ago I planned my networking assignment.")
        self.memory.db.execute("UPDATE turns SET ts=?", (old_ts,))
        self.memory.db.commit()
        self.memory.add_turn("user", "This is today's active conversation and must stay raw.")

        result = self.service.consolidate_daily(older_than_days=3, limit_days=3)

        self.assertEqual(result["turns_deleted"], 1)
        remaining = self.memory.list_turns(10)
        self.assertEqual(len(remaining), 1)
        self.assertIn("today's active conversation", remaining[0]["text"])
        note = self.memory.notes_for_periods("daily", (datetime.date.today() - datetime.timedelta(days=4)).isoformat(), (datetime.date.today() - datetime.timedelta(days=4)).isoformat())
        self.assertEqual(len(note), 1)

    def test_failed_summary_never_deletes_raw_turns(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        ts = datetime.datetime.combine(yesterday, datetime.time(12, 0)).timestamp()
        self.memory.add_turn("user", "Keep this raw turn if summarization fails.")
        self.memory.db.execute("UPDATE turns SET ts=?", (ts,))
        self.memory.db.commit()
        self.service.llm = FakeLLM(fail=True)
        result = self.service.consolidate_daily(today=datetime.date.today())
        self.assertEqual(result["turns_deleted"], 0)
        self.assertEqual(len(self.memory.list_turns(10)), 1)

    def test_personality_change_is_whitelisted_and_rollback_works(self):
        self.service.ensure_baseline()
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.memory.upsert_note("daily", yesterday, "The user appreciated patient follow-up questions.")
        result = self.service.evolve_personality(yesterday)
        self.assertEqual(result["status"], "accepted")
        with open(self.service.character_path, encoding="utf-8") as f:
            evolved = json.load(f)
        self.assertIn("patient with follow-up questions", evolved["traits"])
        self.assertEqual(evolved["values"], self.service.memory.list_personality_versions(1)[0]["character"]["values"])
        rolled = self.service.rollback_latest()
        self.assertTrue(rolled["restored"])
        with open(self.service.character_path, encoding="utf-8") as f:
            restored = json.load(f)
        self.assertNotIn("patient with follow-up questions", restored["traits"])

    def test_due_reminder_claim_marks_it_once(self):
        import time
        self.memory.add_reminder("Drink water", time.time() - 2)
        first = self.memory.claim_due_reminders()
        second = self.memory.claim_due_reminders()
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
