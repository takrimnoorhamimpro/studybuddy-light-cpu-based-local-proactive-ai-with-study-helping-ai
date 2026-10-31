"""Privacy-first camera access policy. No capture is started by importing this module."""
from dataclasses import dataclass
import time


@dataclass
class CameraPolicy:
    enabled: bool = False
    minimum_interval_minutes: int = 5
    last_started_at: float = 0.0

    def can_start(self, now=None):
        if not self.enabled:
            return False, "Camera access is disabled in Settings."
        now = time.time() if now is None else float(now)
        cooldown = max(0, int(self.minimum_interval_minutes)) * 60
        remaining = cooldown - (now - self.last_started_at)
        if self.last_started_at and remaining > 0:
            return False, f"Please wait {int(remaining + 0.999)} seconds before opening the camera again."
        return True, "OK"

    def mark_started(self, now=None):
        self.last_started_at = time.time() if now is None else float(now)
