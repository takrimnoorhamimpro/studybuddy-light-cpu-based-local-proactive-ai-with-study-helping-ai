"""Regression tests for the four requested v11 Phase 0 review fixes."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _studybuddy_test_support import PROJECT_ROOT
ROOT = str(PROJECT_ROOT)
from chat_core import ChatCore
from memory import Memory
from intent_router import IntentRouter
from companion.privacy import PrivacyService


class FakeClock:
    def __init__(self, value=1000.0): self.value = value
    def __call__(self): return self.value
    def advance(self, seconds): self.value += seconds


class FakeLLM:
    def __init__(self): self.calls = 0
    def chat(self, messages):
        self.calls += 1
        return "normal chat response"


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(":memory:")
        self.llm = FakeLLM()
        self.core = ChatCore(ROOT, llm=self.llm, memory=self.memory)
        class RouteSpy:
            def __init__(inner, llm): inner.calls = 0; inner.llm = llm
            def tools_for_text(inner, text):
                inner.calls += 1
                return []
            def run(inner, messages):
                return inner.llm.chat(messages)
        self.routes = RouteSpy(self.llm)
        self.core.tool_orchestrator = self.routes

    def tearDown(self): self.memory.close()

    def test_dialog_and_intent_vocabulary_is_english_allow_list_only(self):
        approved = {
            "add", "assignment", "begin", "can you", "cancel", "cancel it",
            "complete", "completed", "correct", "could you", "countdown", "create",
            "do it", "do not", "don't", "done", "finish", "finished", "focus session",
            "focus timer", "forget it", "include", "jot down", "later", "make",
            "mark as complete", "mark complete", "mark done", "nah", "need", "never mind",
            "nevermind", "no", "no need", "nope", "not interested", "not now",
            "notification", "notify", "notify me", "nudge", "nudge me", "ok", "okay",
            "pass", "please do", "please remind", "pomodoro", "put", "remind", "remind me",
            "reminder", "reminders", "run", "save", "set", "skip", "start", "stop",
            "sure", "task", "task list", "tasks", "timer", "to-do", "todo", "want",
            "would like", "yeah", "yep", "yes", "yup",
            "why did you message me", "not helpful", "less often", "stop these", "leave me alone",
        }
        path = os.path.join(ROOT, "config", "intent_dictionary.json")
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
        entries = []
        for key in ("yes_phrases", "no_phrases", "cancel_phrases", "skip_phrases", "initiative_feedback_phrases"):
            entries.extend(config.get(key, []))
        for intent in config["intents"]:
            for group in intent.get("keyword_groups", []):
                self.assertTrue(group, f"Empty keyword group in {intent['id']}")
                entries.extend(group)
        for entry in entries:
            self.assertTrue(entry.isascii(), f"Non-ASCII vocabulary entry: {entry!r}")
            self.assertIn(entry, approved, f"Vocabulary entry is not allow-listed: {entry!r}")

    def test_onboarding_expires_after_five_minutes_and_current_message_reaches_chat(self):
        clock = FakeClock()
        router = IntentRouter(os.path.join(ROOT, "config", "intent_dictionary.json"), clock=clock)
        self.core.intent_router = router
        router.start_onboarding()
        clock.advance(301)
        result = self.core.handle_message("explain TCP handshake")
        self.assertEqual(result["reply"], "normal chat response")
        self.assertFalse(router.has_pending)
        self.assertEqual(self.llm.calls, 1)

    def test_has_pending_expires_onboarding_but_not_other_dialogs(self):
        clock = FakeClock()
        router = IntentRouter(os.path.join(ROOT, "config", "intent_dictionary.json"), clock=clock)
        router.start_onboarding()
        clock.advance(299)
        self.assertTrue(router.has_pending)
        clock.advance(101)
        self.assertFalse(router.has_pending)
        self.assertIsNone(router.state)

        intent = router.config["intents"][0]
        router.start(intent, "original action")
        clock.advance(400)
        self.assertTrue(router.has_pending)
        self.assertIsNotNone(router.state)

    def test_long_or_question_message_releases_availability_dialog(self):
        for message in ("explain TCP handshake", "Why do you need my name?"):
            self.core.intent_router.start_onboarding()
            result = self.core.handle_message(message)
            self.assertNotIn("Just say yes", result["reply"])
            self.assertFalse(self.core.intent_router.has_pending)
        self.assertEqual(self.llm.calls, 2)

    def test_two_ambiguous_onboarding_replies_release_dialog(self):
        router = self.core.intent_router
        router.start_onboarding()
        first = router.advance("hmm")
        self.assertEqual(first["kind"], "reply")
        self.assertIn("yes, no, or skip", first["reply"].lower())
        second = router.advance("hmm")
        self.assertEqual(second["kind"], "delegate_current")
        self.assertEqual(second["original_text"], "hmm")
        self.assertFalse(router.has_pending)

    def test_onboarding_english_yes_no_skip_remain_deterministic(self):
        router = self.core.intent_router
        for phrase in ("skip", "not now", "later", "stop"):
            router.start_onboarding()
            result = self.core.handle_message(phrase)
            self.assertFalse(router.has_pending)
            self.assertTrue(any(token in result["reply"].lower() for token in ("leave onboarding alone", "skipped")))
        router.start_onboarding()
        self.assertIn("What name", self.core.handle_message("yes")["reply"])
        result = self.core.handle_message("no")
        self.assertFalse(router.has_pending)
        self.assertIn("won't ask", result["reply"].lower())
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(self.routes.calls, 0)

    def test_name_prefixes_are_cleaned_and_second_yes_is_required(self):
        router = self.core.intent_router
        for phrase, expected in (("my name is Takrim", "Takrim"), ("call me Tak", "Tak")):
            router.start_onboarding()
            router.advance("yes")
            result = router.advance(phrase)
            self.assertEqual(router.state["candidate_label"], "Preferred name: " + expected)
            self.assertEqual(result["kind"], "reply")
            self.assertEqual(router.state["phase"], "confirm_memory")
            save = router.advance("yes")
            self.assertEqual(save["kind"], "save_companion_fact")
            self.assertEqual(save["label"], "Preferred name: " + expected)

    def test_name_no_declines_yes_reasks_and_invalid_names_do_not_save(self):
        router = self.core.intent_router
        # A fresh dialog: "no" during name collection politely ends it.
        router.start_onboarding(); router.advance("yes")
        self.assertIn("won't ask", router.advance("no")["reply"].lower())
        router.start_onboarding(); router.advance("yes")
        self.assertIn("name you'd like", router.advance("yes")["reply"].lower())
        router.start_onboarding(); router.advance("yes")
        for invalid in ("This name is definitely far too many words to accept", "Takrim123", "T@k"):
            router.start_onboarding(); router.advance("yes")
            result = router.advance(invalid)
            self.assertIn(result["kind"], ("reply", "delegate_current"))
            if router.state is not None:
                self.assertNotIn("candidate_label", router.state)
            self.assertNotEqual(result["kind"], "save_companion_fact")

    def test_name_question_is_delegated_to_normal_chat(self):
        self.core.intent_router.start_onboarding()
        self.core.handle_message("yes")
        result = self.core.handle_message("why do you need my name?")
        self.assertEqual(result["reply"], "normal chat response")
        self.assertFalse(self.core.intent_router.has_pending)

    def test_hmm_removed_from_global_yes_and_tool_confirm_does_not_accept_it(self):
        path = os.path.join(ROOT, "config", "intent_dictionary.json")
        with open(path, encoding="utf-8") as handle: config = json.load(handle)
        self.assertNotIn("hmm", config["yes_phrases"])
        intent = config["intents"][0]
        router = self.core.intent_router
        router.start(intent, "original action")
        result = router.advance("hmm")
        self.assertEqual(result["kind"], "reply")
        self.assertIn("reply YES", result["reply"])
        self.assertTrue(router.has_pending)
        router.clear()
        router.start_onboarding()
        result = router.advance("hmm")
        self.assertEqual(result["kind"], "reply")
        self.assertIn("yes, no, or skip", result["reply"].lower())

    def _seed_forget_data(self):
        self.memory.add_turn("user", "physics was discussed")
        self.memory.add_fact("physics fact")
        self.memory.add_note("daily", "day", "physics note")
        self.memory.add_task("physics homework", details="physics chapter")
        self.memory.add_reminder("physics revision", 9999999999)
        db = self.memory.db
        db.execute("CREATE TABLE IF NOT EXISTS personal_nodes(id TEXT PRIMARY KEY,label TEXT,evidence TEXT,kind TEXT,status TEXT,consented INTEGER,created_ts REAL,updated_ts REAL)")
        db.execute("INSERT OR IGNORE INTO personal_nodes (id,label,kind,status,evidence,consented,created_ts,updated_ts) VALUES('physics-node','physics node','topic','confirmed','physics evidence',1,1,1)")
        db.execute("CREATE TABLE IF NOT EXISTS personal_edges(id TEXT PRIMARY KEY,source_id TEXT,target_id TEXT,relation TEXT,evidence TEXT,created_ts REAL)")
        db.execute("INSERT OR IGNORE INTO personal_nodes (id,label,kind,status,evidence,consented,created_ts,updated_ts) VALUES('other-node','other node','topic','candidate','other evidence',0,1,1)")
        db.execute("CREATE TABLE IF NOT EXISTS companion_followups(id TEXT PRIMARY KEY,due_ts REAL,kind TEXT,payload TEXT,status TEXT,created_ts REAL,delivered_ts REAL,claimed_ts REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS open_loops(id TEXT PRIMARY KEY,title TEXT,kind TEXT,due_ts REAL,status TEXT,source TEXT,node_id TEXT,created_ts REAL,last_surfaced_ts REAL,surfaced_count INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS companion_events(id INTEGER PRIMARY KEY,ts REAL,kind TEXT,trigger TEXT,reason TEXT,meta TEXT)")
        db.execute("INSERT INTO personal_edges VALUES('e1','physics-node','other-node','related','ordinary evidence',1)")
        db.execute("INSERT INTO companion_followups VALUES('f1',9999999999,'onboarding','physics payload','pending',1,NULL,NULL)")
        db.execute("INSERT INTO open_loops (id,title,kind,due_ts,status,source,node_id,created_ts,last_surfaced_ts,surfaced_count) VALUES('l1','physics loop','exam',NULL,'open','manual','physics-node',1,NULL,0)")
        db.execute("INSERT INTO companion_events (id,ts,kind,trigger,reason,meta) VALUES(1,1,'test',NULL,'physics reason','{}')")
        db.commit()

    def test_forget_default_preserves_tasks_and_reminders(self):
        self._seed_forget_data()
        self.memory.forget("physics")
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE '%physics%' OR details LIKE '%physics%'").fetchone()[0], 1)
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM reminders WHERE text LIKE '%physics%'").fetchone()[0], 1)
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM turns WHERE text LIKE '%physics%'").fetchone()[0], 0)

    def test_forget_include_tasks_true_deletes_tasks_and_reminders(self):
        self._seed_forget_data()
        self.memory.forget("physics", include_tasks=True)
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE '%physics%' OR details LIKE '%physics%'").fetchone()[0], 0)
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM reminders WHERE text LIKE '%physics%'").fetchone()[0], 0)

    def test_preview_forget_counts_without_mutating_data(self):
        self._seed_forget_data()
        preview = PrivacyService(self.memory).preview_forget("physics")
        self.assertEqual(preview["turns"], 1)
        self.assertEqual(preview["facts"], 1)
        self.assertEqual(preview["notes"], 1)
        self.assertEqual(preview["personal_nodes"], 1)
        self.assertEqual(preview["personal_edges"], 1)
        self.assertEqual(preview["open_loops"], 1)
        self.assertEqual(preview["tasks"], 1)
        self.assertEqual(preview["reminders"], 1)
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 1)
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_short_keyword_preview_and_forget_are_noops(self):
        self._seed_forget_data()
        before = self.memory.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        self.assertEqual(PrivacyService(self.memory).forget_topic("hi"), 0)
        self.assertTrue(all(value == 0 for value in PrivacyService(self.memory).preview_forget("hi").values()))
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], before)


if __name__ == "__main__": unittest.main()
