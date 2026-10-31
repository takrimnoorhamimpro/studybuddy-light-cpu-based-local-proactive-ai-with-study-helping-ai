"""
test_memory.py  -  Tests for memory v1 (uses an in-RAM database, writes nothing).

    python app/test_memory.py
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _studybuddy_test_support import PROJECT_ROOT
BASE_DIR = str(PROJECT_ROOT)

from memory import Memory  # noqa: E402
from chat_core import ChatCore  # noqa: E402
from llm import trim_to_sentence  # noqa: E402


class FakeLLM:
    def __init__(self, reply="ok"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


class TestMemory(unittest.TestCase):
    def test_turns_come_back_oldest_first(self):
        m = Memory(":memory:")
        m.add_turn("user", "one")
        m.add_turn("assistant", "two")
        m.add_turn("user", "three")
        got = m.recent_turns(2)
        self.assertEqual([t["content"] for t in got], ["two", "three"])

    def test_facts_no_duplicates(self):
        m = Memory(":memory:")
        self.assertTrue(m.add_fact("Exam on 20 Nov"))
        self.assertFalse(m.add_fact("Exam on 20 Nov"))
        self.assertEqual(m.list_facts(), ["Exam on 20 Nov"])

    def test_trusted_people(self):
        m = Memory(":memory:")
        m.add_trusted("Rahim")
        self.assertFalse(m.add_trusted("rahim"))   # same name, any case
        self.assertEqual(m.trusted_names(), ["Rahim"])

    def test_search_finds_relevant_note_only(self):
        m = Memory(":memory:")
        m.add_note("daily", "2026-10-01", "Talked about the physics exam and feeling nervous")
        m.add_note("daily", "2026-10-02", "Watched a football match with Rahim")
        got = m.search_notes("how did the physics exam go?")
        self.assertEqual(len(got), 1)
        self.assertIn("physics", got[0])

    def test_search_with_only_stopwords_returns_nothing(self):
        m = Memory(":memory:")
        m.add_note("daily", "2026-10-01", "something")
        self.assertEqual(m.search_notes("what is this"), [])

    def test_search_tasks_ranks_open_tasks_by_context_not_fixed_phrases(self):
        m = Memory(":memory:")
        m.add_task("Prepare physics practice set", "Review Newton's laws")
        m.add_task("Buy groceries", "Milk and bread")
        results = m.search_tasks("YouTube physics lecture about Newton's laws", limit=3)
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "Prepare physics practice set")
        self.assertTrue(all(item["status"] == "open" for item in results))
        self.assertEqual(m.search_tasks("YouTube physics lecture", status="done"), [])

    def test_search_survives_odd_characters(self):
        m = Memory(":memory:")
        m.add_note("daily", "2026-10-01", "Had a quiz")
        m.search_notes('quiz" OR * ( ) NEAR')   # must not crash

    def test_forget_removes_everything_matching(self):
        m = Memory(":memory:")
        m.add_turn("user", "my secret project is Zebra")
        m.add_fact("Secret project Zebra")
        m.add_note("daily", "2026-10-01", "Worked on Zebra today")
        m.add_note("daily", "2026-10-02", "Went for a walk")
        self.assertEqual(m.forget("Zebra"), 3)
        self.assertEqual(m.recent_turns(10), [])
        self.assertEqual(m.list_facts(), [])
        self.assertEqual(m.search_notes("Zebra"), [])
        self.assertEqual(len(m.search_notes("walk")), 1)   # others untouched

    def test_forget_ignores_tiny_keywords(self):
        m = Memory(":memory:")
        m.add_fact("a fact")
        self.assertEqual(m.forget("a"), 0)

    def test_tasks_and_reminders_persist(self):
        m = Memory(":memory:")
        task_id = m.add_task("Submit assignment", due_ts=2000000000)
        self.assertTrue(task_id)
        self.assertEqual(m.list_tasks()[0]["title"], "Submit assignment")
        self.assertTrue(m.complete_task(task_id))
        self.assertEqual(m.list_tasks(), [])
        self.assertEqual(m.list_tasks(include_done=True)[0]["status"], "done")
        reminder_id = m.add_reminder("Drink water", 100)
        self.assertTrue(reminder_id)
        due = m.due_reminders(101)
        self.assertEqual(due[0]["text"], "Drink water")
        m.mark_reminder_fired(reminder_id)
        self.assertEqual(m.due_reminders(101), [])

    def test_data_survives_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "memory.db")
            m = Memory(path)
            m.add_fact("Likes tea")
            m.close()
            m2 = Memory(path)
            self.assertEqual(m2.list_facts(), ["Likes tea"])
            m2.close()


class TestCoreWithMemory(unittest.TestCase):
    def test_reminder_command_is_handled_without_model(self):
        m = Memory(":memory:")
        fake = FakeLLM("should not be used")
        core = ChatCore(BASE_DIR, llm=fake, memory=m)
        r = core.handle_message("remind me in 25 minutes to drink water")
        self.assertIn("remind you", r["reply"].lower())
        self.assertEqual(len(fake.calls), 0)
        self.assertEqual(m.list_reminders()[0]["text"], "drink water")

    def test_add_task_command_is_handled_without_model(self):
        m = Memory(":memory:")
        fake = FakeLLM("should not be used")
        core = ChatCore(BASE_DIR, llm=fake, memory=m)
        r = core.handle_message("add task: finish assignment due 2026-11-01 18:00")
        self.assertIn("added", r["reply"].lower())
        self.assertEqual(m.list_tasks()[0]["title"], "finish assignment")
        self.assertEqual(len(fake.calls), 0)

    def test_turns_saved_and_restored(self):
        m = Memory(":memory:")
        core = ChatCore(BASE_DIR, llm=FakeLLM("hi!"), memory=m)
        core.handle_message("hello there")
        core2 = ChatCore(BASE_DIR, llm=FakeLLM(), memory=m)   # fresh session
        self.assertEqual(core2.history, [])  # persistent turns are searched only on explicit request
        self.assertGreaterEqual(len(m.recent_turns(2)), 2)  # durable records remain available

    def test_facts_and_notes_reach_the_prompt(self):
        m = Memory(":memory:")
        m.add_fact("Exam on 20 Nov")
        m.add_note("daily", "2026-10-01", "Worried about the chemistry quiz")
        fake = FakeLLM()
        core = ChatCore(BASE_DIR, llm=fake, memory=m)
        core.settings["max_prompt_facts"] = 4
        core.settings["retrieved_notes"] = 2
        core.handle_message("What do you know about my chemistry quiz and exam?")
        system = fake.calls[0][0]["content"]
        self.assertIn("Exam on 20 Nov", system)
        self.assertIn("chemistry quiz", system)

    def test_trusted_names_used_in_high_risk_reply(self):
        m = Memory(":memory:")
        m.add_trusted("Rahim")
        core = ChatCore(BASE_DIR, llm=FakeLLM(), memory=m)
        r = core.handle_message("I want to kill myself")
        self.assertEqual(r["flag"], "high")
        self.assertIn("Rahim", r["reply"])

    def test_add_trusted_person_updates_core(self):
        core = ChatCore(BASE_DIR, llm=FakeLLM(), memory=Memory(":memory:"))
        self.assertTrue(core.add_trusted_person("Mother"))
        self.assertEqual(core.trusted_names, ["Mother"])

    def test_without_memory_greeting_uses_fast_path(self):
        fake = FakeLLM("yo")
        core = ChatCore(BASE_DIR, llm=fake)
        result = core.handle_message("hey")
        self.assertEqual(result["flag"], "greeting")
        self.assertNotEqual(result["reply"], "yo")
        self.assertEqual(len(fake.calls), 0)
        self.assertFalse(core.add_trusted_person("Rahim"))


class TestOldHistory(unittest.TestCase):
    def test_old_turns_are_not_restored(self):
        m = Memory(":memory:")
        m.add_turn("user", "I feel so hopeless")
        m.add_turn("assistant", "I'm here")
        # pretend those turns happened 2 days ago
        m.db.execute("UPDATE turns SET ts = ts - 172800")
        m.add_turn("user", "fresh message")
        got = m.recent_turns(10, max_age_hours=6)
        self.assertEqual([t["content"] for t in got], ["fresh message"])
        self.assertEqual(len(m.recent_turns(10)), 3)   # no limit = all

    def test_core_starts_fresh_after_long_gap(self):
        m = Memory(":memory:")
        m.add_turn("user", "old heavy chat")
        m.db.execute("UPDATE turns SET ts = ts - 172800")
        core = ChatCore(BASE_DIR, llm=FakeLLM(), memory=m)
        self.assertEqual(core.history, [])

    def test_prompt_says_facts_are_about_user(self):
        m = Memory(":memory:")
        m.add_fact("Exam on 20 Nov")
        fake = FakeLLM()
        core = ChatCore(BASE_DIR, llm=fake, memory=m)
        core.settings["max_prompt_facts"] = 4
        core.handle_message("What do you know about my exam facts?")
        system = fake.calls[0][0]["content"]
        self.assertIn("about THEM", system)
        self.assertIn("Never invent a name", system)


class TestTrim(unittest.TestCase):
    def test_cut_reply_keeps_full_sentences(self):
        self.assertEqual(trim_to_sentence("Hello there. How are you? I was thin"),
                         "Hello there. How are you?")

    def test_complete_reply_unchanged(self):
        self.assertEqual(trim_to_sentence("All good."), "All good.")

    def test_no_sentence_end_keeps_text(self):
        self.assertEqual(trim_to_sentence("no punctuation here"), "no punctuation here")


if __name__ == "__main__":
    unittest.main(verbosity=2)
