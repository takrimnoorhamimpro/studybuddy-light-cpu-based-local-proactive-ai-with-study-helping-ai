import os
import tempfile
import unittest

from intelligence.tool_monitor import ToolMonitor


class _SampleTool:
    def run(self, value):
        if value == "fail":
            raise RuntimeError("expected failure")
        return value.upper()


class ToolMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = ToolMonitor(os.path.join(self.tmp.name, "telemetry.sqlite3"))

    def tearDown(self):
        self.monitor.close()
        self.tmp.cleanup()

    def test_track_success_and_failure(self):
        with self.monitor.track("one", "Successful action", "test", "content omitted"):
            pass
        with self.assertRaises(RuntimeError):
            with self.monitor.track("two", "Failed action", "test"):
                raise RuntimeError("expected failure")
        rows = self.monitor.recent_events(10)
        self.assertEqual([row["status"] for row in rows], ["failed", "success"])
        self.assertEqual(self.monitor.stats()["failed"], 1)

    def test_instrument_wraps_bound_method_once_and_preserves_result(self):
        tool = _SampleTool()
        self.assertTrue(self.monitor.instrument(tool, "run", "sample", "Sample", "test"))
        self.assertFalse(self.monitor.instrument(tool, "run", "sample", "Sample", "test"))
        self.assertEqual(tool.run("hello"), "HELLO")
        self.assertEqual(self.monitor.recent_events(1)[0]["status"], "success")


if __name__ == "__main__":
    unittest.main()
