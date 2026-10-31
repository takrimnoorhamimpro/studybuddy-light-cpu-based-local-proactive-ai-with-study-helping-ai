"""Headless checks for the interactive architecture visualizer data model."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from intelligence.dashboard import ARCHITECTURE_NODES


class DashboardArchitectureTests(unittest.TestCase):
    def test_visualizer_has_one_primary_llm_node(self):
        llm_nodes = [node_id for node_id, node in ARCHITECTURE_NODES.items()
                     if "primary LLM" in (node.get("description", "") + " " + node.get("title", "")).casefold()
                     or node_id == "llm"]
        self.assertEqual(llm_nodes, ["llm"])
        self.assertNotIn("local_ai", ARCHITECTURE_NODES)
        self.assertNotIn("router_llm", ARCHITECTURE_NODES)

    def test_visualizer_includes_editable_intent_dialog_and_low_priority_proactive_path(self):
        self.assertIn("intent", ARCHITECTURE_NODES)
        self.assertIn("editable keyword dictionary", ARCHITECTURE_NODES["intent"]["subtitle"].casefold())
        self.assertIn("proactive", ARCHITECTURE_NODES)
        self.assertIn("low-priority", ARCHITECTURE_NODES["proactive"]["subtitle"].casefold())


    def test_proactive_architecture_excludes_past_chat_memory(self):
        spec = ARCHITECTURE_NODES["proactive"]
        text = (spec["subtitle"] + " " + spec["description"]).casefold()
        self.assertIn("fresh sanitized app/title metadata", text)
        self.assertIn("no past chats", text)
        self.assertIn("one-turn context", text)

    def test_every_architecture_node_has_live_description(self):
        for node_id, node in ARCHITECTURE_NODES.items():
            self.assertTrue(node.get("title"), node_id)
            self.assertTrue(node.get("subtitle"), node_id)
            self.assertTrue(node.get("description"), node_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
