import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from proactive_policy import ProactiveRateLimiter


class ProactiveRateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.now = [1_800_000_000.0]
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "proactive_state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kwargs):
        return ProactiveRateLimiter(
            self.path, global_cooldown_seconds=3600,
            same_app_cooldown_seconds=10800, attempt_cooldown_seconds=900,
            daily_limit=3, clock=lambda: self.now[0], **kwargs
        )

    def test_same_app_title_changes_cannot_bypass_persistent_cooldown(self):
        limiter = self.build()
        self.assertTrue(limiter.can_attempt("chrome", "Media"))
        limiter.record_attempt("chrome", "Media")
        limiter.record_sent("chrome", "Media", "Looks like you may be watching a physics lecture.")

        # New window title is intentionally not part of the cooldown key.
        reloaded = self.build()
        self.assertFalse(reloaded.can_attempt("chrome", "Media"))

    def test_global_cooldown_applies_across_different_apps(self):
        limiter = self.build()
        limiter.record_attempt("chrome", "Media")
        limiter.record_sent("chrome", "Media", "Looks like you may be watching a physics lecture.")
        self.now[0] += 1800
        self.assertFalse(limiter.can_attempt("code", "Coding"))
        self.now[0] += 1801
        self.assertTrue(limiter.can_attempt("code", "Coding"))

    def test_exact_repeat_message_is_detected_for_24_hours(self):
        limiter = self.build()
        text = "Looks like you may be watching a physics lecture."
        self.assertFalse(limiter.is_duplicate(text))
        limiter.record_sent("chrome", "Media", text)
        self.assertTrue(limiter.is_duplicate(text))
        self.now[0] += 86401
        self.assertFalse(limiter.is_duplicate(text))

    def test_daily_attempt_cap(self):
        limiter = self.build()
        for i in range(3):
            self.assertTrue(limiter.can_attempt("app" + str(i), "Browser"))
            limiter.record_attempt("app" + str(i), "Browser")
            self.now[0] += 901
        self.assertFalse(limiter.can_attempt("another", "Browser"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
