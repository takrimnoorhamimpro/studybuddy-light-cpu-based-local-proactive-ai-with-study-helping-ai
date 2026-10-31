"""Phase 0 regression tests: consent dialogs, follow-up delivery and privacy cascade."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from chat_core import ChatCore
from memory import Memory
from personal_companion import PersonalCompanion
from companion.privacy import PrivacyService
from intent_router import IntentRouter

from _studybuddy_test_support import PROJECT_ROOT
ROOT = str(PROJECT_ROOT)


class FakeLLM:
    def __init__(self): self.calls = 0
    def chat(self, messages):
        self.calls += 1
        return "fake response"


class Phase0Tests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(":memory:")
        self.pc = PersonalCompanion(self.memory)
        self.llm = FakeLLM()
        self.core = ChatCore(ROOT, llm=self.llm, memory=self.memory)
        class RouteSpy:
            calls = 0
            def tools_for_text(inner, text):
                inner.calls += 1
                return ["should_not_be_used"]
        self.route_spy = RouteSpy()
        self.core.tool_orchestrator = self.route_spy

    def tearDown(self): self.memory.close()

    def test_onboarding_yes_no_skip_are_deterministic_no_llm(self):
        self.core.intent_router.start_onboarding()
        result = self.core.handle_message("skip")
        self.assertIn("skipped", result["reply"].lower())
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(self.route_spy.calls, 0)
        self.assertFalse(self.core.intent_router.has_pending)

        self.core.intent_router.start_onboarding()
        result = self.core.handle_message("no")
        self.assertIn("No problem", result["reply"])
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(self.route_spy.calls, 0)

        self.core.intent_router.start_onboarding()
        result = self.core.handle_message("yes")
        self.assertIn("What name", result["reply"])
        self.assertEqual(self.llm.calls, 0)
        result = self.core.handle_message("Takrim")
        self.assertIn("remember", result["reply"].lower())
        result = self.core.handle_message("yes")
        self.assertIn("explicitly agreed", result["reply"])
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(self.route_spy.calls, 0)
        saved = self.pc.list_nodes(include_candidates=False)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["label"], "Preferred name: Takrim")

    def test_followup_claim_ack_and_release(self):
        fid = self.pc.schedule_followup(time.time() - 1, payload={"topic": "onboarding"})
        claimed = self.pc.claim_due_followups(kinds=("onboarding",))
        self.assertEqual([row["id"] for row in claimed], [fid])
        self.assertEqual(self.pc.claim_due_followups(kinds=("onboarding",)), [])
        self.assertTrue(self.pc.release_followup(fid))
        claimed = self.pc.claim_due_followups(kinds=("onboarding",))
        self.assertEqual(len(claimed), 1)
        self.assertTrue(self.pc.acknowledge_followup(fid))
        self.assertEqual(self.pc.claim_due_followups(kinds=("onboarding",)), [])

    def test_followups_dedupe_pending_and_unknown_kind_stays_pending(self):
        first = self.pc.schedule_followup(time.time() + 500, payload={"x": 1})
        second = self.pc.schedule_followup(time.time() + 100, payload={"x": 2})
        self.assertEqual(first, second)
        self.pc.schedule_followup(time.time() - 1, kind="future_kind")
        self.assertEqual(self.pc.claim_due_followups(kinds=("onboarding",)), [])
        row = self.memory.db.execute("SELECT status FROM companion_followups WHERE kind='future_kind'").fetchone()
        self.assertEqual(row["status"], "pending")

    def test_forget_cascades_to_companion_graph_and_followups(self):
        node = self.pc.add_fact("Zebra exam", "deadline", evidence="Zebra course", confirmed=True, consented=True)
        other = self.pc.add_fact("Other", "topic")
        self.pc.add_relation(node, other, "related to Zebra")
        self.pc.schedule_followup(time.time() + 10, payload={"topic": "Zebra"})
        self.memory.add_turn("user", "Zebra exam is coming")
        self.memory.add_fact("Zebra exam")
        removed = self.memory.forget("Zebra")
        self.assertGreaterEqual(removed, 4)
        self.assertFalse(any("Zebra" in n["label"] for n in self.pc.list_nodes()))
        self.assertEqual(self.pc.list_edges(), [])
        self.assertEqual(self.memory.db.execute("SELECT COUNT(*) FROM companion_followups").fetchone()[0], 0)
        ledger = self.memory.db.execute("SELECT note FROM consent_ledger ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(ledger["note"], "topic_forget_requested")
        self.assertNotIn("Zebra", ledger["note"])

    def test_stale_claim_returns_to_pending(self):
        fid = self.pc.schedule_followup(10, payload={"topic": "onboarding"})
        self.assertEqual(len(self.pc.claim_due_followups(now=20, claim_timeout=30)), 1)
        # Simulate a crashed GUI with a claim older than the timeout.
        self.memory.db.execute("UPDATE companion_followups SET claimed_ts=? WHERE id=?", (1, fid))
        self.memory.db.commit()
        reclaimed = self.pc.claim_due_followups(now=100, claim_timeout=30)
        self.assertEqual([row["id"] for row in reclaimed], [fid])

    def test_migration_is_versioned_and_backup_first(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "memory.db")
            mem = Memory(path)
            mem.add_turn("user", "keep me")
            # Build a legacy v10 companion schema with data but without v11 columns.
            mem.db.executescript("""
                CREATE TABLE personal_nodes(id TEXT PRIMARY KEY,label TEXT NOT NULL,kind TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',evidence TEXT NOT NULL DEFAULT '',
                    consented INTEGER NOT NULL DEFAULT 0,created_ts REAL NOT NULL,updated_ts REAL NOT NULL);
                CREATE TABLE personal_edges(id TEXT PRIMARY KEY,source_id TEXT NOT NULL,target_id TEXT NOT NULL,
                    relation TEXT NOT NULL,evidence TEXT NOT NULL DEFAULT '',created_ts REAL NOT NULL);
                CREATE TABLE companion_followups(id TEXT PRIMARY KEY,due_ts REAL NOT NULL,kind TEXT NOT NULL,
                    payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_ts REAL NOT NULL,delivered_ts REAL);
                CREATE TABLE companion_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO personal_nodes VALUES('legacy-node','Keep this graph node','topic','candidate','old evidence',0,1,1);
            """)
            mem.db.commit()
            pc = PersonalCompanion(mem)
            self.assertEqual(mem.db.execute("PRAGMA user_version").fetchone()[0], 3)
            columns = {row[1] for row in mem.db.execute("PRAGMA table_info(personal_nodes)")}
            self.assertIn("importance", columns)
            self.assertIn("sensitivity", columns)
            self.assertEqual(pc.list_nodes()[0]["label"], "Keep this graph node")
            backups = [name for name in os.listdir(temp) if name.startswith("memory.db.bak-")]
            self.assertEqual(len(backups), 1)
            self.assertEqual(mem.recent_turns(2)[0]["content"], "keep me")
            mem.close()

    def test_forget_short_keyword_still_noop(self):
        self.memory.add_turn("user", "hi")
        self.assertEqual(PrivacyService(self.memory).forget_topic("hi"), 0)
        self.assertEqual(len(self.memory.list_turns(5)), 1)


if __name__ == "__main__": unittest.main()
