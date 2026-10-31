import unittest
from features.camera.policy import CameraPolicy


class CameraPolicyTests(unittest.TestCase):
    def test_camera_disabled_by_default(self):
        policy = CameraPolicy(enabled=False)
        allowed, reason = policy.can_start(now=1000)
        self.assertFalse(allowed)
        self.assertIn("disabled", reason.lower())

    def test_enabled_camera_can_start_first_time(self):
        policy = CameraPolicy(enabled=True, minimum_interval_minutes=5)
        self.assertEqual(policy.can_start(now=1000), (True, "OK"))

    def test_cooldown_blocks_repeated_open(self):
        policy = CameraPolicy(enabled=True, minimum_interval_minutes=5)
        policy.mark_started(now=1000)
        allowed, reason = policy.can_start(now=1100)
        self.assertFalse(allowed)
        self.assertIn("200 seconds", reason)

    def test_zero_cooldown_allows_reopen(self):
        policy = CameraPolicy(enabled=True, minimum_interval_minutes=0)
        policy.mark_started(now=1000)
        self.assertTrue(policy.can_start(now=1001)[0])

    def test_cooldown_expires(self):
        policy = CameraPolicy(enabled=True, minimum_interval_minutes=1)
        policy.mark_started(now=1000)
        self.assertTrue(policy.can_start(now=1060)[0])


if __name__ == "__main__":
    unittest.main()
